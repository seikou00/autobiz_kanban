"""Regression cases for extending existing HTTP handlers and registered pages."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from hooks.delivery_report.changes import scan_range, summarize
from hooks.delivery_report.sources import Inputs, Scope
from hooks.delivery_report.source_index import SourceIndex


class DeliveryImpactTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-q')
        self.write('VisitController.java', '''package example;
@RestController
@RequestMapping("/vst-rcd")
public class VisitController {
    private VisitService service;
    @RequestMapping(value="/list", method=RequestMethod.POST)
    public List<VisitDto> list() { return service.list(); }
    @GetMapping("/count")
    public int count() { return 1; }
}''')
        self.write('VisitService.java', '''package example;
public interface VisitService {
    public List<VisitDto> list();
}''')
        self.write('VisitServiceImpl.java', '''package example;
public class VisitServiceImpl implements VisitService {
    private VisitMapper mapper;
    public List<VisitDto> list() { return mapper.list(); }
}''')
        self.write('VisitDto.java', 'package example; public class VisitDto { public String name; }')
        self.write('VisitMapper.java', '''package example;
public interface VisitMapper { public List<VisitDto> list(); }''')
        self.write('VisitMapper.xml', '''<mapper namespace="example.VisitMapper">
<select id="list">select name from visit</select></mapper>''')
        self.write('src/routes.js', '''import Record from './pages/visitPlanRecord';
import Edit from './pages/visitMinutesEdit';
export default [{path:'/visits',component:Record},{path:'/minutes/edit',component:Edit}];''')
        self.write('src/pages/visitPlanRecord.jsx', 'export default function Record(){return <div>list</div>;}')
        self.write('src/pages/visitMinutesEdit.jsx', 'export default function Edit(){return <div>edit</div>;}')
        self.base = self.commit()
        self.git('branch', 'delivery-baseline', self.base)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), '-c', 'user.name=test',
                                        '-c', 'user.email=test@example.invalid', *args], text=True).strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'change')
        return self.git('rev-parse', 'HEAD')

    def changes(self):
        return scan_range(self.root, self.base, self.commit())[1]

    def test_retained_baseline_and_uncommitted_worktree_do_not_change_report_range(self):
        self.write('src/pages/visitPlanRecord.jsx', 'export default function Record(){return <div>committed</div>;}')
        head = self.commit()
        expected = scan_range(self.root, self.base, head)[1]
        self.write('VisitController.java', 'invalid uncommitted source')
        self.assertEqual(scan_range(self.root, self.base, head)[1], expected)
        self.assertEqual(self.git('rev-parse', 'delivery-baseline'), self.base)

    def test_body_and_service_changes_count_existing_handler(self):
        self.write('VisitServiceImpl.java', '''package example;
public class VisitServiceImpl implements VisitService {
    private VisitMapper mapper;
    public List<VisitDto> list() { return enrich(mapper.list()); }
}''')
        changes = self.changes()
        self.assertEqual(set(changes), {('api', 'POST /vst-rcd/list')})
        self.assertEqual(changes['api', 'POST /vst-rcd/list']['operation'], 'modified')
        self.assertIn('VisitServiceImpl.java', changes['api', 'POST /vst-rcd/list']['affectedFiles'])

    def test_return_field_and_mapper_change_propagate_to_handler(self):
        for file, content in [('VisitDto.java', 'package example; public class VisitDto { public String name; public String status; }'),
                              ('VisitMapper.xml', '<mapper namespace="example.VisitMapper"><select id="list">select name,status from visit</select></mapper>')]:
            with self.subTest(file=file):
                self.write(file, content)
                changes = self.changes()
                self.assertEqual(set(changes), {('api', 'POST /vst-rcd/list')})
                self.assertEqual(changes['api', 'POST /vst-rcd/list']['operation'],
                                 'contract_affected' if file.endswith('.java') else 'modified')

    def test_dto_changes_do_not_spread_through_unused_imports_and_other_methods(self):
        self.write('OtherController.java', '''package example;
@RestController public class OtherController {
    @GetMapping("/unrelated") public int unrelated() { return 1; }
    public VisitDto unused() { return null; }
}''')
        self.base = self.commit()
        self.write('VisitDto.java', 'package example; public class VisitDto { public String name; public String status; }')
        changes = self.changes()
        self.assertEqual(set(changes), {('api', 'POST /vst-rcd/list')})
        self.assertEqual(changes['api', 'POST /vst-rcd/list']['operation'], 'contract_affected')

    def test_confirmed_contract_changes_enter_headline_without_double_counting(self):
        self.write('VisitDto.java', 'package example; public class VisitDto { public String name; public String status; }')
        head = self.commit()
        src = Inputs(Scope(self.root, 'alpha', self.root, None, []))
        metrics, _ = summarize([{'ref': 'service', 'root': str(self.root), 'baseSha': self.base, 'headSha': head,
                                 'sourceRefs': ['manifest.json']}], src)
        self.assertEqual(metrics['api']['value'], 1)
        self.assertEqual(metrics['apiModified']['value'], 1)
        self.assertEqual(metrics['apiContractAffected']['value'], 1)
        self.assertEqual(src.gaps, [])

    def test_mapper_parse_issue_does_not_invalidate_known_added_and_deleted_counts(self):
        self.write('VisitMapper.xml', '<mapper namespace="example.VisitMapper"><select id="list">broken')
        head = self.commit()
        src = Inputs(Scope(self.root, 'alpha', self.root, None, []))
        metrics, _ = summarize([{'ref': 'service', 'root': str(self.root), 'baseSha': self.base, 'headSha': head,
                                 'sourceRefs': ['manifest.json']}], src)
        self.assertEqual(metrics['apiAdded']['status'], 'measured')
        self.assertEqual(metrics['apiDeleted']['status'], 'measured')
        self.assertEqual(metrics['apiModified']['status'], 'partial')
        self.assertEqual(src.gaps, [])
        self.assertEqual(len(src.diagnostics), 1)

    def test_capitalized_pages_and_child_component_count_one_page(self):
        self.write('ui/src/Pages/visit/inputVisitPlan.jsx', "import Contact from './children/addMailContacter'; export default function Input(){return <Contact/>;}")
        self.write('ui/src/Pages/visit/children/addMailContacter.jsx', 'export default function Contact(){return <div>old</div>;}')
        self.base = self.commit()
        self.write('ui/src/Pages/visit/children/addMailContacter.jsx', 'export default function Contact(){return <div>new</div>;}')
        changes = self.changes()
        self.assertEqual(set(changes), {('page', '文件 ui/src/Pages/visit/inputVisitPlan.jsx')})

    def test_local_mapper_fragment_and_namespaced_include(self):
        self.write('queries.xml', '<mapper namespace="shared"><sql id="fields">name</sql></mapper>')
        self.write('VisitMapper.xml', '''<!DOCTYPE mapper [<!ENTITY body SYSTEM "content.xml">]>
<mapper namespace="example.VisitMapper">&body;</mapper>''')
        self.write('content.xml', '<select id="list">select <include refid="shared.fields"/> from visit</select>')
        self.base = self.commit()
        self.write('queries.xml', '<mapper namespace="shared"><sql id="fields">name,status</sql></mapper>')
        changes = self.changes()
        self.assertEqual(changes['api', 'POST /vst-rcd/list']['operation'], 'modified')

    def test_identical_routes_in_distinct_services_keep_their_identity(self):
        text = 'package example; @RestController public class Controller { @GetMapping("/same") public int same(){return 1;} }'
        definitions = SourceIndex({'a/src/main/java/Controller.java': text, 'b/src/main/java/Controller.java': text}).definitions()
        self.assertIn(('api', 'a · GET /same'), definitions)
        self.assertIn(('api', 'b · GET /same'), definitions)

    def test_unchanged_routes_detect_modified_and_new_pages(self):
        self.write('src/pages/visitPlanRecord.jsx', 'export default function Record(){return <div>list with status</div>;}')
        self.write('src/views/inputVisitPlan.jsx', 'export default function Input(){return <form/>;}')
        changes = self.changes()
        self.assertEqual(changes['page', '/visits']['operation'], 'modified')
        self.assertEqual(changes['page', '文件 src/views/inputVisitPlan.jsx']['operation'], 'added')
        self.assertNotIn(('page', '/minutes/edit'), changes)
        self.assertNotIn(('page', '文件 src/pages/visitPlanRecord.jsx'), changes)

    def test_lazy_component_under_relative_nested_route(self):
        self.write('src/routes.js', "const Record=lazy(()=>import('./pages/visitPlanRecord')); export default [{path:'/visits',children:[{path:'list',component:Record}]}];")
        self.base = self.commit()
        self.write('src/pages/visitPlanRecord.jsx', 'export default function Record(){return <div>updated</div>;}')
        changes = self.changes()
        self.assertEqual(set(changes), {('page', '/visits/list')})
        self.assertEqual(changes['page', '/visits/list']['operation'], 'modified')

    def test_added_and_deleted_api_are_independent_of_modified_api(self):
        self.write('VisitController.java', '''package example;
@RestController @RequestMapping("/vst-rcd")
public class VisitController {
    private VisitService service;
    @PostMapping("/list") public List<VisitDto> list() { return enrich(service.list()); }
    @GetMapping(path={"/new","/another"}) public int created() { return 2; }
}''')
        changes = self.changes()
        self.assertEqual(changes['api', 'GET /vst-rcd/count']['operation'], 'deleted')
        self.assertEqual(changes['api', 'POST /vst-rcd/list']['operation'], 'modified')
        self.assertEqual(changes['api', 'GET /vst-rcd/new']['operation'], 'added')
        self.assertEqual(changes['api', 'GET /vst-rcd/another']['operation'], 'added')

    def test_implicit_public_service_and_mapper_methods_reach_unchanged_controller(self):
        self.write('VisitService.java', 'package example; public interface VisitService { List<VisitDto> list(); }')
        self.write('VisitMapper.java', 'package example; public interface VisitMapper { List<VisitDto> list(); }')
        self.base = self.commit()
        self.write('VisitMapper.xml', '<mapper namespace="example.VisitMapper"><select id="list">select name,status from visit</select></mapper>')
        changes = self.changes()
        change = changes['api', 'POST /vst-rcd/list']
        self.assertEqual(change['operation'], 'modified')
        evidence = next(e for e in change['implementationEvidence'] if e['kind'] == 'mapper')
        self.assertEqual([n['name'] for n in evidence['chain']],
                         ['VisitController#list', 'VisitService#list', 'VisitServiceImpl#list', 'VisitMapper#list', 'SQL list'])
        self.assertEqual(change['affectedFiles'], ['VisitMapper.xml'])

    def test_package_private_helper_logic_changes_count_only_reached_entry(self):
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return build();} List<VisitDto> build(){return null;} int unused(){return 1;} }')
        self.base = self.commit()
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return build();} List<VisitDto> build(){throw new IllegalStateException();} int unused(){return 1;} }')
        changes = self.changes()
        self.assertEqual(set(changes), {('api','POST /vst-rcd/list')})
        self.assertTrue(any(e['name'] == 'VisitServiceImpl#build' for e in changes['api','POST /vst-rcd/list']['implementationEvidence']))

    def test_unused_method_change_does_not_count_other_methods_of_service(self):
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return null;} int unused(){return 1;} }')
        self.base = self.commit()
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return null;} int unused(){return 99;} }')
        self.assertEqual(self.changes(), {})

    def test_method_reference_follows_referenced_business_helper(self):
        old = 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return items.stream().map(this::convert).toList();} VisitDto convert(Object item){return null;} }'
        self.write('VisitServiceImpl.java', old)
        self.base = self.commit()
        self.write('VisitServiceImpl.java', old.replace('return null;', 'return new VisitDto();'))
        self.assertEqual(self.changes()['api','POST /vst-rcd/list']['operation'], 'modified')

    def test_inherited_service_method_and_super_call_are_followed(self):
        self.write('BaseVisitService.java', 'package example; public class BaseVisitService { protected List<VisitDto> build(){return null;} }')
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl extends BaseVisitService implements VisitService { public List<VisitDto> list(){return super.build();} }')
        self.base = self.commit()
        self.write('BaseVisitService.java', 'package example; public class BaseVisitService { protected List<VisitDto> build(){return new ArrayList<>();} }')
        change = self.changes()['api','POST /vst-rcd/list']
        self.assertEqual(change['operation'],'modified')
        self.assertIn('BaseVisitService.java', change['affectedFiles'])

    def test_overloads_with_different_argument_counts_are_resolved(self):
        old = 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return build("x");} List<VisitDto> build(){return null;} List<VisitDto> build(String key){return null;} }'
        self.write('VisitServiceImpl.java', old)
        self.base = self.commit()
        self.write('VisitServiceImpl.java', old.replace('build(String key){return null;}', 'build(String key){return new ArrayList<>();}'))
        _, changes, _, warnings = scan_range(self.root,self.base,self.commit())
        self.assertEqual(changes['api','POST /vst-rcd/list']['operation'],'modified')
        self.assertFalse(any('重载' in w['reason'] for w in warnings),warnings)

    def test_qualified_implementation_does_not_fan_out_to_other_beans(self):
        self.write('VisitController.java', 'package example; @RestController public class VisitController { @Qualifier("primaryVisit") private VisitService service; @GetMapping("/visits") public List<VisitDto> list(){return service.list();} }')
        self.write('VisitServiceImpl.java', 'package example; @Service("primaryVisit") public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return null;} }')
        self.write('OtherVisitService.java', 'package example; @Service("otherVisit") public class OtherVisitService implements VisitService { public List<VisitDto> list(){return null;} }')
        self.base = self.commit()
        self.write('OtherVisitService.java', 'package example; @Service("otherVisit") public class OtherVisitService implements VisitService { public List<VisitDto> list(){return new ArrayList<>();} }')
        self.assertEqual(self.changes(),{})
        self.write('VisitServiceImpl.java', 'package example; @Service("primaryVisit") public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return new ArrayList<>();} }')
        _, changes, _, warnings = scan_range(self.root,self.base,self.commit())
        self.assertEqual(changes['api','GET /visits']['operation'],'modified')
        self.assertFalse(any('多个实现' in w['reason'] for w in warnings), warnings)

    def test_chained_local_return_type_keeps_the_call_chain(self):
        self.write('Worker.java','package example; public class Worker { List<VisitDto> query(){return null;} }')
        self.write('VisitServiceImpl.java','package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return worker().query();} Worker worker(){return new Worker();} }')
        self.base = self.commit()
        self.write('Worker.java','package example; public class Worker { List<VisitDto> query(){return new ArrayList<>();} }')
        self.assertEqual(self.changes()['api','POST /vst-rcd/list']['operation'],'modified')

    def test_only_referenced_enum_constant_is_an_implementation_change(self):
        self.write('ErrorCode.java','package example; public enum ErrorCode { USED("old"), UNUSED("old"); ErrorCode(String message){} }')
        self.write('VisitServiceImpl.java','package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){throw new Failure(ErrorCode.USED);} }')
        self.base = self.commit()
        self.write('ErrorCode.java','package example; public enum ErrorCode { USED("old"), UNUSED("new"); ErrorCode(String message){} }')
        self.assertEqual(self.changes()['api','POST /vst-rcd/list']['operation'],'potential_affected')
        self.write('ErrorCode.java','package example; public enum ErrorCode { USED("new"), UNUSED("old"); ErrorCode(String message){} }')
        change = self.changes()['api','POST /vst-rcd/list']
        self.assertEqual(change['operation'],'modified')
        self.assertTrue(any(e['name'] == 'ErrorCode#USED' for e in change['implementationEvidence']))

    def test_comments_and_formatting_do_not_count_as_api_logic_changes(self):
        self.write('VisitServiceImpl.java', 'package example; public class VisitServiceImpl implements VisitService { private VisitMapper mapper; public List<VisitDto> list() { /* explanatory only */ return mapper.list(); } }')
        self.assertEqual(self.changes(),{})

    def test_six_confirmed_changes_with_no_controller_diff(self):
        methods = ['save','summary','upload','remove','count']
        controller = 'package example; @RestController public class VisitController { private VisitService service;'
        controller += ''.join('@PostMapping("/'+n+'") public int '+n+'(){return service.'+n+'();}' for n in methods)
        controller += '@GetMapping("/detail") public VisitDto detail(){return service.detail();} }'
        self.write('VisitController.java',controller)
        self.write('VisitService.java','package example; public interface VisitService {'+''.join('int '+n+'();' for n in methods)+'VisitDto detail();}')
        implementation = 'package example; public class VisitServiceImpl implements VisitService {'+''.join('public int '+n+'(){return 1;}' for n in methods)+'public VisitDto detail(){return null;} }'
        self.write('VisitServiceImpl.java',implementation)
        self.base = self.commit()
        self.write('VisitServiceImpl.java',implementation.replace('return 1;', 'return 2;'))
        self.write('VisitDto.java','package example; public class VisitDto { public String name; public String status; }')
        head = self.commit()
        src = Inputs(Scope(self.root,'alpha',self.root,None,[]))
        metrics, details = summarize([{'ref':'service','root':str(self.root),'baseSha':self.base,'headSha':head,'sourceRefs':['manifest.json']}],src)
        self.assertEqual(metrics['apiModified']['value'],6)
        self.assertEqual(metrics['api']['value'],6)
        self.assertEqual(metrics['apiImplementationModified']['value'],5)
        self.assertEqual(metrics['apiContractModified']['value'],1)
        self.assertEqual(len(details[0]['changes']),6)
        self.assertNotIn('VisitController.java', [f['path'] for f in details[0]['files']])

    def test_identical_types_in_other_modules_do_not_cross_link(self):
        sources = {}
        for module in ('one','two'):
            prefix=module+'/src/main/java/'
            sources[prefix+'Api.java']='package example; @RestController public class Api { private Service service; @GetMapping("/list") public int list(){return service.list();} }'
            sources[prefix+'Service.java']='package example; public interface Service { int list(); }'
            sources[prefix+'Impl.java']='package example; public class Impl implements Service { public int list(){return 1;} }'
        before=SourceIndex(sources).definitions()
        sources['one/src/main/java/Impl.java']=sources['one/src/main/java/Impl.java'].replace('return 1;','return 2;')
        after=SourceIndex(sources).definitions()
        self.assertNotEqual(before['api','one · GET /list']['fingerprint'],after['api','one · GET /list']['fingerprint'])
        self.assertEqual(before['api','two · GET /list']['fingerprint'],after['api','two · GET /list']['fingerprint'])

    def test_same_arity_overloads_use_declared_parameter_type(self):
        old = 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return build("x");} List<VisitDto> build(int key){return null;} List<VisitDto> build(String key){return null;} }'
        self.write('VisitServiceImpl.java',old)
        self.base=self.commit()
        self.write('VisitServiceImpl.java',old.replace('build(String key){return null;}', 'build(String key){return new ArrayList<>();}'))
        _,changes,_,warnings=scan_range(self.root,self.base,self.commit())
        self.assertEqual(changes['api','POST /vst-rcd/list']['operation'],'modified')
        self.assertFalse(any('重载' in w['reason'] for w in warnings),warnings)

    def test_ambiguous_same_arity_overload_keeps_partial_status(self):
        old = 'package example; public class VisitServiceImpl implements VisitService { public List<VisitDto> list(){return build(unknown);} List<VisitDto> build(Integer key){return null;} List<VisitDto> build(String key){return null;} }'
        self.write('VisitServiceImpl.java',old)
        self.base=self.commit()
        self.write('VisitServiceImpl.java',old.replace('build(String key){return null;}', 'build(String key){return new ArrayList<>();}'))
        _,_,_,warnings=scan_range(self.root,self.base,self.commit())
        self.assertTrue(any('重载' in w['reason'] and w['affectsMetric'] for w in warnings),warnings)

    def test_local_xml_entity_keeps_changed_fragment_in_sql_evidence(self):
        self.write('VisitMapper.xml', '<!DOCTYPE mapper [<!ENTITY body SYSTEM "content.xml">]><mapper namespace="example.VisitMapper">&body;</mapper>')
        self.write('content.xml','<select id="list">select name from visit</select>')
        self.base=self.commit()
        self.write('content.xml','<select id="list">select name,status from visit</select>')
        change=self.changes()['api','POST /vst-rcd/list']
        self.assertEqual(change['operation'],'modified')
        evidence=next(e for e in change['implementationEvidence'] if e['kind']=='mapper')
        self.assertEqual(evidence['path'],'content.xml')
        self.assertIn('content.xml',evidence['sourceFiles'])
        self.assertEqual(evidence['version'],'head')

    def test_referenced_business_threshold_change_reaches_unchanged_handler(self):
        old='package example; public class VisitServiceImpl implements VisitService { private static final int MAX_ROWS=10; public List<VisitDto> list(){return mapper.list(MAX_ROWS);} }'
        self.write('VisitServiceImpl.java',old)
        self.base=self.commit()
        self.write('VisitServiceImpl.java',old.replace('MAX_ROWS=10','MAX_ROWS=20'))
        change=self.changes()['api','POST /vst-rcd/list']
        self.assertEqual(change['operation'],'modified')
        self.assertTrue(any(e['name']=='VisitServiceImpl#MAX_ROWS' for e in change['implementationEvidence']))

    def test_enum_instance_logic_changes_reach_each_actual_caller(self):
        self.write('ErrorCode.java','package example; public enum ErrorCode { USED("x"); ErrorCode(String message){} public int getCode(){return 1;} }')
        self.write('VisitController.java','package example; @RestController public class VisitController {'+''.join('@GetMapping("/error'+str(i)+'") public int error'+str(i)+'(){return ErrorCode.USED.getCode();}' for i in range(4))+'}')
        self.base=self.commit()
        self.write('ErrorCode.java','package example; public enum ErrorCode { USED("x"); ErrorCode(String message){} public int getCode(){return 2;} }')
        changes=self.changes()
        self.assertEqual(len(changes),4)
        self.assertTrue(all(c['operation']=='modified' for c in changes.values()))
        self.assertTrue(all(any(e['name']=='ErrorCode#getCode' for e in c['implementationEvidence']) for c in changes.values()))


if __name__ == '__main__':
    unittest.main()
