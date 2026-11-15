"""Index route definitions and their local source dependencies at a sealed Git tree."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections import defaultdict
from pathlib import PurePosixPath
from xml.etree import ElementTree

from .java import members, semantic_text, arity, enum_constants, split_arguments

SCRIPT_EXTENSIONS = ('.js', '.jsx', '.ts', '.tsx', '.vue')
LITERALS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/')
SPRING_MAPPING = re.compile(r'@(Get|Post|Put|Patch|Delete|Head|Options|Request)Mapping\b')


def mask_literals(text):
    return LITERALS.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)


def block_end(masked, start, opening='{', closing='}'):
    depth = 0
    for offset in range(start, len(masked)):
        if masked[offset] == opening:
            depth += 1
        elif masked[offset] == closing:
            depth -= 1
            if depth == 0:
                return offset + 1
    return len(masked)


def mappings(text):
    """Resolve Spring literal single/multiple paths and explicit HTTP methods."""
    masked = mask_literals(text)
    for match in SPRING_MAPPING.finditer(masked):
        start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
        args = ''
        if start < len(text) and text[start] == '(':
            end = block_end(masked, start, '(', ')')
            args = text[start + 1:end - 1]
        paths_arg = re.search(r'\b(?:value|path)\s*=\s*(\{[^}]*\}|"[^"\n]*")', args)
        raw = paths_arg[1] if paths_arg else args.split(',', 1)[0].strip()
        paths = re.findall(r'"([^"\n]*)"', raw)
        if not paths and not raw:
            paths = ['']
        methods = re.findall(r'RequestMethod\.(\w+)', args) if match[1] == 'Request' else [match[1].upper()]
        yield paths, methods or ['ANY']


class SourceIndex:
    def __init__(self, sources):
        self.sources = sources
        self.nodes = {}
        self.edges = defaultdict(set)
        self.type_edges = defaultdict(set)
        self.contract_types = defaultdict(set)
        self.referenced_types = defaultdict(set)
        self.warnings = []
        self.entries = defaultdict(list)
        self.java = {}
        self.types = defaultdict(list)
        self.methods = defaultdict(list)
        self.parents = defaultdict(list)
        self.fields = {}
        self.method_info = {}
        self.node_info = {}

    def add_node(self, key, path, text):
        self.nodes[key] = (path, hashlib.sha256(text.encode()).hexdigest())

    @staticmethod
    def module(path):
        return path.split('/src/', 1)[0] if '/src/' in path else ''

    def resolve_type(self, owner, name):
        choices = self.types.get(name.rsplit('.', 1)[-1], [])
        imports = owner['imports']
        qualified = [p for p in choices if self.java[p]['qualified'] == name] if '.' in name else []
        explicit = [p for p in choices if self.java[p]['qualified'] in imports]
        local = [p for p in choices if self.java[p]['package'] == owner['package']]
        wildcard = [p for p in choices if self.java[p]['package'] + '.*' in imports]
        preferred = qualified if '.' in name else explicit or local or wildcard
        scoped = [p for p in preferred if self.module(p) == self.module(owner['path'])]
        preferred = scoped or preferred
        return preferred if len(preferred) == 1 else []

    def lookup(self, path, name, count=None, seen=(), parameter_types=None):
        if path in seen:
            return []
        own = [n for n in self.methods[path, name] if count is None or self.method_info[n].count == count]
        if own:
            if len(own) > 1 and parameter_types:
                exact = [n for n in own if all(not given or expected == given for (expected, _), given in zip(self.method_info[n].parameters, parameter_types))]
                if exact:
                    return exact
            return own
        return list({n for parent in self.parents[path] for n in self.lookup(parent, name, count, (*seen, path), parameter_types)})

    def field(self, path, name, seen=()):
        if path in seen:
            return None
        if (path, name) in self.fields:
            return self.fields[path, name]
        choices = [self.field(parent, name, (*seen, path)) for parent in self.parents[path]]
        choices = [n for n in choices if n is not None]
        return choices[0] if len(choices) == 1 else None

    def bindings(self, path, seen=()):
        if path in seen:
            return {}
        inherited = {}
        for parent in self.parents[path]:
            inherited.update(self.bindings(parent, (*seen, path)))
        return {**inherited, **self.java[path]['variables']}

    def qualified_target(self, path, variable, targets):
        field = self.field(path, variable)
        if not field:
            return targets
        declaration = self.sources[field[1]][self.node_info[field]['start']:self.node_info[field]['end']]
        qualifier = re.search(r'@(?:Qualifier|Resource)\s*\(\s*(?:name\s*=\s*)?["\']([^"\']+)', declaration)
        if not qualifier:
            return targets
        implementations = [p for p, owner in self.java.items() if owner['bean'] == qualifier[1]
                           and any(t in self.ancestors(p) for t in targets)]
        return implementations if len(implementations) == 1 else targets

    def ancestors(self, path, seen=()):
        if path in seen:
            return set()
        return {path} | {n for parent in self.parents[path] for n in self.ancestors(parent, (*seen, path))}

    def call_edges(self, path, owner, method, node):
        text = owner['text'][method.body_start + 1:method.finish - 1] if method.ending == '{' else ''
        body = mask_literals(text)
        local = dict((v, t) for t, v in re.findall(r'\b([A-Z]\w*)(?:<[^;{}=]*>)?\s+(\w+)\s*(?=[;=,)])',
                                                owner['masked'][method.start():method.finish]))
        variables = {**self.bindings(path), **local, **{name: typ for typ, name in method.parameters}}
        returned = {}
        def connect(targets, name, count=None, parameter_types=None):
            candidates = list({n for target in targets for n in self.lookup(target, name, count, parameter_types=parameter_types)})
            if len(candidates) == 1:
                self.edges[node].add(candidates[0])
                return candidates
            if len(candidates) > 1:
                self.warnings.append({'kind': 'api', 'files': sorted({path} | set(targets)),
                                      'reason': '同参数数量的方法重载目标待核对', 'affectsMetric': True, '_node': node})
            return []
        for call in re.finditer(r'\b(\w+)\s*\(', body):
            name = call[1]
            opening = body.find('(', call.start())
            close = block_end(body, opening, '(', ')')
            count = arity(body[opening + 1:close - 1], text[opening + 1:close - 1])
            prefix = body[:call.start()].rstrip()
            targets = []
            receiver = re.search(r'([\w.$]+)\s*\.\s*$', prefix)
            chain = re.search(r'\)\s*\.\s*$', prefix)
            if receiver:
                variable = receiver[1]
                if variable == 'this':
                    targets = [path]
                elif variable == 'super':
                    targets = self.parents[path]
                else:
                    variable = variable.removeprefix('this.')
                    typename = variables.get(variable, variable)
                    targets = self.resolve_type(owner, typename)
                    if not targets and '.' in variable:
                        parts = variable.split('.')
                        targets = self.parents[path] if parts[0] == 'super' else self.resolve_type(owner, variables.get(parts[0], parts[0]))
                        for member in parts[1:]:
                            nested = []
                            for target in targets:
                                field = self.field(target, member)
                                if field:
                                    self.edges[node].add(field)
                                    field_owner = self.java[field[1]]
                                    nested.extend(self.resolve_type(field_owner, field_owner['variables'].get(member, '')))
                            targets = nested
                    if not targets and len(self.types.get(typename.rsplit('.', 1)[-1], [])) > 1:
                        self.warnings.append({'kind': 'api', 'reason': '本地调用类型存在歧义：' + typename,
                                              'files': sorted({path} | set(self.types[typename.rsplit('.', 1)[-1]])),
                                              'affectsMetric': True, '_node': node})
                    targets = self.qualified_target(path, variable, targets)
            elif chain:
                targets = returned.get(prefix.rfind(')') + 1, [])
            elif not prefix.endswith('.') and name not in {'if','for','while','switch','catch','new','synchronized'}:
                targets = [path]
            argument_types = []
            for argument in split_arguments(body[opening + 1:close - 1], text[opening + 1:close - 1]):
                literal = 'String' if argument.startswith('"') else 'char' if argument.startswith("'") else 'boolean' if argument in {'true','false'} else 'int' if re.fullmatch(r'-?\d+', argument) else None
                created = re.match(r'new\s+(\w+)', argument)
                argument_types.append(literal or (created[1] if created else variables.get(argument)))
            linked = connect(targets, name, count, argument_types)
            returned[close] = list({p for n in linked if self.method_info[n].returns
                                    for p in self.resolve_type(self.java[n[1]], self.method_info[n].returns)})
        for receiver, name in re.findall(r'\b(\w+)\s*::\s*(\w+)', body):
            targets = [path] if receiver == 'this' else self.parents[path] if receiver == 'super' else self.resolve_type(owner, variables.get(receiver, receiver))
            connect(self.qualified_target(path, receiver, targets), name)
        # Follow referenced fields/constants, without adding every field of a shared DTO.
        for variable in self.bindings(path):
            field = self.field(path, variable)
            if field and ((variable not in local and re.search(r'(?<![\w.])' + re.escape(variable) + r'\b', body))
                          or re.search(r'\bthis\.' + re.escape(variable) + r'\b', body)):
                self.edges[node].add(field)
        for receiver, variable in re.findall(r'\b(\w+)\s*\.\s*(\w+)\b(?!\s*\()', body):
            for target in self.resolve_type(owner, variables.get(receiver, receiver)):
                field = self.field(target, variable)
                if field:
                    self.edges[node].add(field)

    def index_java(self):
        for path, text in self.sources.items():
            if not path.endswith('.java'):
                continue
            masked = mask_literals(text)
            cls = re.search(r'\b(?:class|interface|record|enum)\s+(\w+)', masked)
            if not cls:
                continue
            package = re.search(r'\bpackage\s+([\w.]+)', masked)
            pkg = package[1] if package else ''
            bean = re.search(r'@(?:Service|Component|Repository)\s*\(\s*["\']([^"\']+)', text[:cls.start()])
            owner = self.java[path] = {
                'path': path, 'name': cls[1], 'package': pkg, 'qualified': (pkg + '.' if pkg else '') + cls[1],
                'imports': re.findall(r'\bimport\s+([\w.*]+)', masked), 'text': text,
                'masked': masked, 'classStart': cls.start(), 'methods': [], 'variables': {},
                'bean': bean[1] if bean else cls[1][:1].lower() + cls[1][1:],
            }
            self.types[cls[1]].append(path)
            methods, fields = members(text, masked, cls.start())
            constants = enum_constants(masked, cls.start())
            fields.extend(constants)
            constant_names = {name for name, _, _ in constants}
            for method in methods:
                node = ('method', path, method.name, method.start())
                self.method_info[node] = method
                self.node_info[node] = {'kind': 'method', 'path': path, 'name': cls[1] + '#' + method.name,
                                        'line': text.count('\n', 0, method.start()) + 1,
                                        'identity': semantic_text(text[method.start():method.body_start])}
                self.add_node(node, path, semantic_text(text[method.annotation_begin:method.finish]))
                self.methods[path, method.name].append(node)
                owner['methods'].append((method, method.finish, node))
            for name, start, end in fields:
                node = ('field', path, name)
                self.fields[path, name] = node
                declaration = masked[start:end]
                types = re.findall(r'\b([A-Z]\w*)(?:<[^;{}=]*>)?\s+' + re.escape(name) + r'\b', declaration)
                owner['variables'][name] = types[-1] if types else cls[1] if name in constant_names else ''
                self.add_node(node, path, semantic_text(text[start:end]))
                self.node_info[node] = {'kind': 'field', 'path': path, 'name': cls[1] + '#' + name,
                                        'line': text.count('\n', 0, start) + 1, 'start': start, 'end': end}
        for path, owner in self.java.items():
            header = owner['masked'][owner['classStart']:].split('{', 1)[0]
            while re.search(r'<[^<>]*>', header):
                header = re.sub(r'<[^<>]*>', '', header)
            parent_header = re.split(r'\b(?:extends|implements)\b', header, maxsplit=1)
            parents = re.findall(r'\b[A-Z]\w*', parent_header[1]) if len(parent_header) > 1 else []
            self.parents[path] = [p for parent in parents for p in self.resolve_type(owner, parent)]
            shape = owner['text'][owner['classStart']:]
            for method, end, _ in reversed(owner['methods']):
                shape = shape[:method.annotation_begin - owner['classStart']] + shape[end - owner['classStart']:]
            self.add_node(('type', path), path, semantic_text(shape))
            for typename in set(re.findall(r'\b[A-Z]\w*\b', mask_literals(shape))):
                self.type_edges['type', path].update(('type', p) for p in self.resolve_type(owner, typename) if p != path)
        # Build implementation links only after all inheritance and method declarations exist.
        for path, owner in self.java.items():
            for ancestor in self.ancestors(path) - {path}:
                for declaration, _, source in self.java[ancestor]['methods']:
                    for target in self.lookup(path, declaration.name, declaration.count, parameter_types=[typ for typ, _ in declaration.parameters]):
                        if target != source and self.method_info[target].ending == '{':
                            self.edges[source].add(target)
            annotation = ('annotation', path)
            prefix = owner['text'][:owner['classStart']].rsplit(';', 1)[-1]
            self.add_node(annotation, path, semantic_text(prefix))
            self.node_info[annotation] = {'kind': 'annotation', 'path': path, 'name': owner['name'] + ' 类级注解', 'line': 1}
            previous_end = owner['masked'].find('{', owner['classStart']) + 1
            for method, end, node in owner['methods']:
                self.edges[node].add(annotation)
                self.call_edges(path, owner, method, node)
                body = owner['masked'][method.start():end]
                for typename in set(re.findall(r'\b[A-Z]\w*\b', body)):
                    self.referenced_types[node].update(('type', p) for p in self.resolve_type(owner, typename) if p != path)
                signature = owner['masked'][method.start():method.body_start]
                for typename in set(re.findall(r'\b[A-Z]\w*\b', signature)):
                    self.contract_types[node].update(('type', p) for p in self.resolve_type(owner, typename) if p != path)
                annotations = owner['text'][previous_end:method.start()]
                previous_end = end
                prefix = list(mappings(owner['text'][:owner['classStart']]))
                prefixes = prefix[-1][0] if prefix else ['']
                for paths, verbs in mappings(annotations):
                    for base in prefixes:
                        for route in paths:
                            full = '/' + '/'.join(part.strip('/') for part in (base, route) if part.strip('/'))
                            for verb in verbs:
                                self.entries['api', verb + ' ' + full].append((path, node))
        declarations = {node for owner in self.java.values() for method, _, node in owner['methods'] if method.ending == ';'}
        for declaration in declarations:
            targets = {n for n in self.edges[declaration] if n[0] == 'method' and n != declaration}
            if len(targets) > 1:
                self.edges[declaration].difference_update(targets)
                self.warnings.append({'kind': 'api', 'reason': '接口方法存在多个实现，调用目标待核对',
                                      'files': sorted({declaration[1]} | {n[1] for n in targets}), 'affectsMetric': True, '_node': declaration})
        registered = {path for (kind, _), entries in self.entries.items() if kind == 'api' for path, _ in entries}
        for path, owner in self.java.items():
            if path not in registered and SPRING_MAPPING.search(owner['masked'][owner['classStart']:]):
                self.warnings.append({'kind': 'api', 'reason': '接口映射存在未解析的入口', 'files': [path],
                                      'affectsMetric': True, 'metrics': ['api', 'apiAdded', 'apiModified', 'apiDeleted']})
        self.index_mappers()

    def mapper_document(self, path, seen=()):
        if path in seen:
            raise ValueError('本地 Mapper 片段存在循环引用')
        text = self.sources[path]
        for name, target in re.findall(r'<!ENTITY\s+(\w+)\s+SYSTEM\s+["\']([^"\']+)["\']\s*>', text):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if resolved not in self.sources:
                raise ValueError('本地 Mapper 片段不可定位：' + target)
            fragment = self.mapper_document(resolved, (*seen, path))
            fragment = re.sub(r'<\?xml[^>]*\?>', '', fragment)
            text = text.replace('&' + name + ';', fragment)
            self.edges['mapper_file', path].add(('mapper_file', resolved))
        return re.sub(r'<!DOCTYPE[\s\S]*?\]>|<!DOCTYPE[^>]*>', '', text)

    def index_mappers(self):
        statements, references = {}, []
        for path, text in self.sources.items():
            if not path.endswith('.xml') or '<mapper' not in text:
                continue
            try:
                root = ElementTree.fromstring(self.mapper_document(path))
            except (ElementTree.ParseError, ValueError) as exc:
                self.warnings.append({'kind': 'api', 'reason': str(exc), 'files': [path], 'affectsMetric': True})
                continue
            namespace = root.get('namespace')
            owners = [p for p, j in self.java.items() if j['qualified'] == namespace]
            for element in root:
                identifier = element.get('id')
                if not identifier:
                    continue
                node = ('mapper', path, identifier)
                self.add_node(node, path, ElementTree.tostring(element, encoding='unicode'))
                origins = [p for kind, p in self.reachable(('mapper_file', path)) if kind == 'mapper_file']
                origin = next((p for p in sorted(origins) if re.search(r'<\w+\b[^>]*\bid=[\"\']' + re.escape(identifier) + r'[\"\']', self.sources.get(p, ''))), path)
                location = re.search(r'<\w+\b[^>]*\bid=[\"\']' + re.escape(identifier) + r'[\"\']', self.sources[origin])
                self.node_info[node] = {'kind': 'mapper', 'path': origin, 'name': 'SQL ' + identifier,
                                        'line': self.sources[origin].count('\n', 0, location.start()) + 1 if location else None}
                # Included local fragments also remain visible as provenance paths.
                for dependency in self.reachable(('mapper_file', path)):
                    if dependency[1] != path:
                        self.add_node(dependency, dependency[1], '')
                        self.edges[node].add(dependency)
                statements[(namespace or '') + '.' + identifier] = node
                for reference in element.iter():
                    for key in ('refid', 'resultMap'):
                        target = reference.get(key)
                        if target:
                            references.append((node, target if '.' in target else (namespace or '') + '.' + target))
                for owner in owners:
                    for method in self.methods[owner, identifier]:
                        self.edges[method].add(node)
        for node, target in references:
            if target in statements:
                self.edges[node].add(statements[target])
            else:
                self.warnings.append({'kind': 'api', 'reason': 'Mapper 引用不可定位：' + target,
                                      'files': [node[1]], 'affectsMetric': True})

    def resolve_script(self, path, target):
        if target.startswith('.'):
            candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            roots = [candidate]
        elif target.startswith(('@/', '~/')):
            roots = [target[2:], 'src/' + target[2:]]
            parts = path.split('/')
            if 'src' in parts:
                roots.insert(0, '/'.join(parts[:parts.index('src') + 1]) + '/' + target[2:])
        else:
            return None
        for root in roots:
            for candidate in [root] + [root + ext for ext in SCRIPT_EXTENSIONS] + [root + '/index' + ext for ext in SCRIPT_EXTENSIONS]:
                if candidate in self.sources:
                    return candidate
        return None

    def index_pages(self):
        scripts = {p: t for p, t in self.sources.items() if p.endswith(SCRIPT_EXTENSIONS)}
        imported = {}
        for path, text in self.sources.items():
            self.add_node(('file', path), path, text)
        for path, text in scripts.items():
            self.add_node(('file', path), path, text)
            bindings = {}
            pattern = r'\b(?:import|export)\s+([^;\n]*?)\s+from\s*["\']([^"\']+)["\']|\b(?:import|require)\s*\(\s*["\']([^"\']+)["\']\s*\)'
            for match in re.finditer(pattern, text):
                target = self.resolve_script(path, match[2] or match[3])
                if target:
                    self.edges['file', path].add(('file', target))
                    for name in re.findall(r'\b\w+\b', match[1] or ''):
                        bindings[name] = target
            for module in re.findall(r'''\bimport\s*["']([^"']+)["']''', text):
                target = self.resolve_script(path, module)
                if target:
                    self.edges['file', path].add(('file', target))
            for binding, module in re.findall(r"""\b(?:const|let|var)\s+(\w+)\s*=\s*[^;\n]*?\bimport\s*\(\s*["']([^"']+)["']""", text):
                target = self.resolve_script(path, module)
                if target:
                    bindings[binding] = target
            imported[path] = bindings
        registered = set()
        for path, text in scripts.items():
            # Route objects, JSX Route elements and dynamic imports are resolved against the whole tree.
            masked = mask_literals(text)
            routes = []
            for match in re.finditer(r'\bpath\s*[:=]\s*["\']([^"\'\r\n]*)["\']', text):
                start = masked.rfind('{', 0, match.start())
                jsx = text.rfind('<Route', 0, match.start())
                if jsx > text.rfind('>', 0, match.start()):
                    start, end = jsx, text.find('>', match.end()) + 1
                elif start >= 0:
                    end = block_end(masked, start)
                else:
                    continue
                routes.append((match, start, end))
            for match, start, end in routes:
                fragment = re.split(r'\bchildren\s*:', text[start:end], maxsplit=1)[0]
                if not re.search(r'\b(component|element|loadComponent|lazy)\b|<Route', fragment):
                    continue
                node = ('route', path, match.start())
                self.add_node(node, path, fragment)
                dependencies = {target for name, target in imported[path].items() if re.search(r'\b' + re.escape(name) + r'\b', fragment)}
                for module in re.findall(r'\b(?:import|require)\s*\(\s*["\']([^"\']+)["\']', fragment):
                    resolved = self.resolve_script(path, module)
                    if resolved:
                        dependencies.add(resolved)
                self.edges[node].update(('file', p) for p in dependencies)
                registered.update(self.reachable(node))
                route = match[1]
                parents = [(m, s, e) for m, s, e in routes if s < start < e]
                for parent, _, _ in sorted(parents, key=lambda row: row[1], reverse=True):
                    if route.startswith('/'):
                        break
                    route = parent[1].rstrip('/') + '/' + route
                route = '/' + route.lstrip('/')
                self.entries['page', route].append((sorted(dependencies)[0] if dependencies else path, node))
        candidates = []
        for path, text in scripts.items():
            parts = {part.casefold() for part in PurePosixPath(path).parts}
            if (parts & {'pages', 'views', 'screens'} and not parts & {'components', 'children', '__tests__', 'test', 'tests'}
                    and (path.endswith(('.jsx', '.tsx', '.vue')) or re.search(r'export\s+default', text))):
                candidates.append(path)
        for path in candidates:
            if ('file', path) in registered:
                continue
            node = ('file', path)
            self.entries['page', '文件 ' + path].append((path, node))
            registered.update(self.reachable(node))

    def reachable(self, node, edges=None):
        edges = self.edges if edges is None else edges
        seen, todo = set(), [node]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            todo.extend(edges[current] - seen)
        return seen

    def fingerprint(self, nodes):
        contents = sorted(self.nodes[n] for n in nodes if n in self.nodes)
        return hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()

    def describe_node(self, node):
        info = self.node_info.get(node, {})
        return {'kind': info.get('kind', node[0]), 'path': info.get('path', node[1]),
                'name': info.get('name', ('SQL ' if node[0] == 'mapper' else '') + str(node[2] if len(node) > 2 else node[0])),
                'line': info.get('line')}

    def evidence(self, record, changed_paths):
        predecessors = {node: None for node in sorted(record['_roots'], key=str)}
        queue = list(predecessors)
        for node in queue:
            for target in sorted(self.edges[node], key=str):
                if target not in predecessors:
                    predecessors[target] = node
                    queue.append(target)
        result = {}
        for node in record['_reachable']:
            if node not in self.nodes or (node[1] not in changed_paths and node[0] != 'mapper'):
                continue
            info = self.node_info.get(node, {})
            identity = (node[0], node[1], info.get('identity') or info.get('name') or str(node[2:]))
            key = json.dumps(identity, ensure_ascii=False)
            chain, current = [], node
            while current is not None:
                chain.append(self.describe_node(current))
                current = predecessors.get(current)
            result[key] = {**self.describe_node(node), 'fingerprint': self.nodes[node][1], 'chain': list(reversed(chain)),
                           'sourceFiles': sorted({self.nodes[n][0] for n in self.reachable(node) if n in self.nodes} & changed_paths)}
        return result

    def definitions(self):
        self.index_java()
        self.index_pages()
        result = {}
        api_reachable = set()
        for (kind, name), entries in self.entries.items():
            groups = defaultdict(list)
            for path, node in entries:
                service = path.split('/src/', 1)[0] if kind == 'api' and '/src/' in path else ''
                groups[service].append((path, node))
            for service, scoped in groups.items():
                reachable = set().union(*(self.reachable(node) for _, node in scoped))
                if kind == 'api':
                    api_reachable.update(reachable)
                contracts = set().union(*(self.contract_types[node] for _, node in scoped))
                referenced = set().union(*(self.referenced_types[node] for node in reachable))
                contracts = set().union(*(self.reachable(node, self.type_edges) for node in contracts)) if contracts else set()
                referenced = set().union(*(self.reachable(node, self.type_edges) for node in referenced)) if referenced else set()
                key = (kind, service + ' · ' + name if service else name)
                result[key] = {'path': scoped[0][0], 'service': service,
                               'files': sorted({self.nodes[n][0] for n in reachable if n in self.nodes}),
                               'contractFiles': sorted({self.nodes[n][0] for n in contracts if n in self.nodes}),
                               'impactFiles': sorted({self.nodes[n][0] for n in referenced if n in self.nodes}),
                               'fingerprint': self.fingerprint(reachable),
                               'contractFingerprint': self.fingerprint(contracts),
                               '_graph': self if kind == 'api' else None, '_reachable': reachable if kind == 'api' else set(),
                               '_roots': [node for _, node in scoped] if kind == 'api' else [],
                               'impactFingerprint': hashlib.sha256(json.dumps(
                                   sorted((self.nodes[n][0], self.sources[self.nodes[n][0]]) for n in referenced if n in self.nodes),
                                   sort_keys=True).encode()).hexdigest()}
        self.warnings = [{k:v for k,v in w.items() if k != '_node'} for w in self.warnings
                         if '_node' not in w or w['_node'] in api_reachable]
        return result
