"""Static analysis of foreground build scripts; never evaluates shell code.

This recognizes commands and ordinary logging/status wrappers independently.
Unknown executable statements remain visible to the caller's stage policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import posixpath
import re
import shlex


def normalize_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    path = re.sub(r"^/([a-zA-Z])/(?=.)", r"\1:/", path)
    path = posixpath.normpath(path) if path else ""
    windows = re.match(r"^[a-zA-Z]:/", path) is not None
    if path and (os.name == "nt" or (path.startswith("/") and not windows)):
        path = Path(path).resolve(strict=False).as_posix()
    return path.casefold() if os.name == "nt" or windows else path


def absolute_path(path: str) -> bool:
    return path.startswith("/") or re.match(r"^[a-zA-Z]:/", path) is not None


def executable(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".cmd").removesuffix(".bat")


def redacted_statement(raw: str) -> str:
    # Diagnostics describe syntax, not environment values or credentials.
    raw = re.sub(r'''\b([A-Za-z_][\w]*|-[Dd][\w.-]+)=(?:"(?:\\.|[^"\\])*"|'[^']*'|[^\s;&|]+)''', r"\1=<redacted>", raw)
    raw = re.sub(r"(?i)(--(?:lease-token|password|token|secret|api-key)\s+)(\S+)", r"\1<redacted>", raw)
    return raw[:400]


@dataclass
class Token:
    raw: str
    offset: int
    operator: bool = False

    @property
    def value(self) -> str:
        return shlex.split(self.raw)[0]


@dataclass
class Invocation:
    argv: list[str]
    cwd: str
    line: int
    statement_index: int
    statement: str
    output_files: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    invocations: list[Invocation] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


class ShellSyntaxError(ValueError):
    def __init__(self, reason: str, offset: int):
        super().__init__(reason)
        self.offset = offset


def _tokens(script: str) -> list[Token]:
    """Keep quoted operators/expansions opaque, with source locations."""
    tokens: list[Token] = []
    i = 0
    while i < len(script):
        if script[i] in " \t\r":
            i += 1
            continue
        if script.startswith("\\\n", i):
            i += 2
            continue
        if script[i] == "#":
            end = script.find("\n", i)
            i = end if end >= 0 else len(script)
            continue
        if script[i] in "\n;&|<>()":
            operator = next((op for op in ("&&", "||", ">>", ">&", "&>") if script.startswith(op, i)), script[i])
            tokens.append(Token(operator, i, True))
            i += len(operator)
            continue
        start = i
        quote = ""
        while i < len(script):
            char = script[i]
            if quote == "'":
                if char == "'":
                    quote = ""
                i += 1
                continue
            if char == "\\":
                i += 2
                continue
            if char == "`" or (script.startswith("$(", i) and not script.startswith("$((", i)):
                raise ShellSyntaxError("命令替换会执行额外程序，无法作为生产编译包装语句授权", i)
            if script.startswith("$[", i):
                raise ShellSyntaxError("不支持旧式 $[...] 算术展开；退出码请使用已保存状态的 $((...))", i)
            if script.startswith("$((", i):
                end = script.find("))", i + 3)
                if end < 0:
                    raise ShellSyntaxError("算术展开缺少结束符 ))", i)
                expression = script[i + 3:end]
                if not re.fullmatch(r"[\w\s\[\]$?:!=+\-*/%]+", expression):
                    raise ShellSyntaxError("退出码算术表达式包含不支持的语法", i)
                i = end + 2
                continue
            if script.startswith("${", i):
                end = script.find("}", i + 2)
                if end < 0:
                    raise ShellSyntaxError("变量展开缺少结束符 }", i)
                if not re.fullmatch(r"(?:[A-Za-z_]\w*(?:\[(?:\d+|[@*])\])?|\?)", script[i + 2:end]):
                    raise ShellSyntaxError("变量展开包含无法静态确定的替换或间接引用", i)
                i = end + 1
                continue
            if quote == '"':
                if char == '"':
                    quote = ""
                i += 1
                continue
            if char in "'\"":
                quote = char
                i += 1
                continue
            if char in " \t\r\n;&|<>()":
                break
            i += 1
        if quote:
            raise ShellSyntaxError("引号未闭合", start)
        tokens.append(Token(script[start:i], start))
    return tokens


_ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
_REFERENCE = re.compile(r"\$\{([A-Za-z_]\w*(?:\[\d+\])?)\}|\$([A-Za-z_]\w*)")
_STATUS_VALUE = "\x00saved-pipeline-status"


def _expand(token: Token, variables: dict[str, str | None], *, assignment: bool = False) -> str:
    # Decode quotes while expanding only the parts Bash actually expands.
    # shlex followed by substitution would expand single-quoted literals too.
    raw = token.raw
    value = ""
    quote = ""
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                value += char
            index += 1
            continue
        if char in "'\"" and (not quote or char == quote):
            quote = "" if quote else char
            index += 1
            continue
        if char == "\\" and index + 1 < len(raw):
            next_char = raw[index + 1]
            if quote != '"' or next_char in '$`"\\\n':
                if next_char != "\n":
                    value += next_char
                index += 2
                continue
        if char == "$":
            match = _REFERENCE.match(raw, index)
            if not match:
                raise ValueError("可执行程序或参数包含无法静态确定的 shell 展开")
            name = match.group(1) or match.group(2)
            if name not in variables or variables[name] in {None, _STATUS_VALUE}:
                raise ValueError(f"变量 {name} 的运行时值无法静态确定")
            expanded = str(variables[name])
            if not quote and not assignment and re.search(r"[\s*?\[\]]", expanded):
                raise ValueError(f"未加引号的变量 {name} 会发生分词或路径展开，无法确定真实 argv")
            value += expanded
            index = match.end()
            continue
        if not quote and not assignment and char in "*?[]":
            raise ValueError("未加引号的通配符会改变实际 argv，无法静态确定 Maven 目标")
        value += char
        index += 1
    return value


def _check_status_arithmetic(raw: str, variables: dict[str, str | None]) -> None:
    if "$((" not in raw:
        return
    expression = raw.split("$((", 1)[1].split("))", 1)[0]
    names = re.findall(r"[A-Za-z_]\w*(?:\[\d+\])?", expression)
    if any(name not in variables or (variables[name] != _STATUS_VALUE and not re.fullmatch(r"\d+", str(variables[name]))) for name in names):
        raise ValueError("退出码算术只能引用已经保存的状态变量或纯数字，不能求值任意字符串")


def analyze_script(script: str, cwd: str, *, depth: int = 0) -> Analysis:
    result = Analysis()

    def issue(reason: str, offset: int, index: int = 0, raw: str = "", code: str = "COMMAND_FORM_UNSUPPORTED") -> None:
        result.issues.append({"code": code, "reason": reason, "line": script.count("\n", 0, offset) + 1,
                              "statementIndex": index, "statement": redacted_statement(raw)})

    if depth > 3:
        issue("shell 包装嵌套超过 3 层", 0)
        return result
    try:
        tokens = _tokens(script)
    except (ShellSyntaxError, ValueError) as exc:
        offset = getattr(exc, "offset", 0)
        issue(str(exc), offset, raw=script.splitlines()[script.count("\n", 0, offset)] if script.splitlines() else "")
        return result

    statements: list[tuple[list[Token], str]] = []
    current: list[Token] = []
    for token in tokens:
        if token.operator and token.raw in {";", "\n", "&&", "||", "&"}:
            if current:
                statements.append((current, token.raw))
                current = []
            elif token.raw not in {";", "\n"}:
                issue("控制操作符前缺少命令", token.offset, raw=token.raw)
        else:
            current.append(token)
    if current:
        statements.append((current, ""))
    elif statements and statements[-1][1] in {"&&", "||"}:
        issue("控制操作符后缺少命令", tokens[-1].offset)

    directory = normalize_path(cwd)
    variables: dict[str, str | None] = {"PWD": directory}
    directories = {directory}
    skipped_directories: set[str] = set()
    skipped_variables: list[dict[str, str | None]] = []
    for index, (words, following) in enumerate(statements, 1):
        offset = words[0].offset
        raw = script[offset:words[-1].offset + len(words[-1].raw)]
        before_directories = set(directories)
        before_variables = dict(variables)
        try:
            if following == "&":
                raise ValueError("后台执行无法取得本次编译的前台完成结果")
            if following == "||" and (index >= len(statements) or statements[index][0][0].value != "exit"):
                raise ValueError("|| 分支只支持失败后 exit；其他条件分支的目录或变量无法唯一确定，不能授予编译权限")
            # A PIPESTATUS array is runtime data, not a subshell invocation.
            if len(words) == 4 and _ASSIGNMENT.fullmatch(words[0].raw) and words[1].raw == "(" and words[3].raw == ")":
                if words[2].raw not in {'"${PIPESTATUS[@]}"', '${PIPESTATUS[@]}'}:
                    raise ValueError("数组赋值仅支持保存 ${PIPESTATUS[@]}，不支持子 shell 或任意数组语句")
                name = words[0].raw.split("=", 1)[0]
                variables[name + "[0]"] = variables[name + "[1]"] = _STATUS_VALUE
                continue
            if any(t.operator and t.raw in {"(", ")", "<"} for t in words):
                raise ValueError("不支持子 shell、进程替换或输入重定向作为编译包装")
            prefix = 0
            while prefix < len(words) and _ASSIGNMENT.fullmatch(words[prefix].raw):
                prefix += 1
            if prefix == len(words):
                for token in words:
                    name, value = token.value.split("=", 1)
                    _check_status_arithmetic(token.raw, variables)
                    rhs = token.raw.split("=", 1)[1]
                    rhs = rhs[1:-1] if rhs.startswith('"') and rhs.endswith('"') else rhs
                    reference = _REFERENCE.fullmatch(rhs)
                    status_name = (reference.group(1) or reference.group(2)) if reference else ""
                    if re.fullmatch(r"\$(?:\?|\{PIPESTATUS\[\d+\]\}|\{\?\})", rhs) or (rhs.startswith("$((") and rhs.endswith("))")) or (status_name in variables and variables[status_name] == _STATUS_VALUE):
                        variables[name] = _STATUS_VALUE
                    else:
                        variables[name] = _expand(token, variables, assignment=True).split("=", 1)[1]
                continue
            # Inline assignments affect the child environment, not shell variables.
            for token in words[:prefix]:
                _expand(token, variables, assignment=True)
            words = words[prefix:]
            name = executable(words[0].value)
            if name == "env":
                words = words[1:]
                while words and _ASSIGNMENT.fullmatch(words[0].raw):
                    _expand(words.pop(0), variables)
                if not words or words[0].value.startswith("-"):
                    raise ValueError("env 包装必须使用 NAME=value 和明确的可执行程序，不支持改变执行上下文的选项")
                name = executable(words[0].value)
            if name == "export":
                for token in words[1:]:
                    if not _ASSIGNMENT.fullmatch(token.raw):
                        raise ValueError("export 必须使用显式的 NAME=value 赋值")
                    variable, value = _expand(token, variables, assignment=True).split("=", 1)
                    variables[variable] = value
                continue
            if name == "set":
                if [t.value for t in words[1:]] != ["-o", "pipefail"]:
                    raise ValueError("编译包装只支持 set -o pipefail")
                continue
            if name == "cd":
                args = words[1:]
                if args and args[0].value == "--":
                    args = args[1:]
                if len(args) != 1:
                    raise ValueError("cd 必须指定一个明确的模块目录")
                guarded_exit = following == "||" and index < len(statements) and statements[index][0][0].value == "exit"
                if following != "&&" and not guarded_exit:
                    raise ValueError("cd 后必须使用 && 或 || exit，防止切换目录失败后在错误目录编译")
                path = normalize_path(_expand(args[0], variables))
                if not path:
                    raise ValueError("cd 目录为空")
                directory = path if absolute_path(path) else normalize_path(posixpath.join(directory, path))
                directories = {path if absolute_path(path) else normalize_path(posixpath.join(base, path)) for base in directories}
                variables["PWD"] = directory if len(directories) == 1 else None
                continue
            if name in {"echo", "printf", "exit"}:
                if any(t.operator for t in words):
                    raise ValueError("结果输出/exit 包装中包含额外管道或操作符")
                # Only status arithmetic is accepted, not arbitrary shell arithmetic.
                for token in words[1:]:
                    _check_status_arithmetic(token.raw, variables)
                if name == "exit" and index > 1 and statements[index - 2][1] == "||":
                    # Failures from the preceding &&/|| chain terminate here;
                    # only the successful directory continues after the guard.
                    skipped_directories.clear()
                    skipped_variables.clear()
                continue
            if name in {"bash", "sh", "zsh"}:
                if len(words) != 3 or words[1].value not in {"-c", "-lc", "-cl"} or (not words[2].raw.startswith("'") and "$" in words[2].raw):
                    raise ValueError("shell 包装必须使用静态脚本，例如 bash -lc 'mvn compile'；动态脚本无法检查")
                for base in directories:
                    inner = analyze_script(words[2].value, base, depth=depth + 1)
                    for invocation in inner.invocations:
                        invocation.line += script.count("\n", 0, offset)
                    for failure in inner.issues:
                        failure["line"] += script.count("\n", 0, offset)
                        failure["wrapperLine"] = script.count("\n", 0, offset) + 1
                    result.invocations.extend(inner.invocations)
                    result.issues.extend(inner.issues)
                continue

            segments: list[list[Token]] = [[]]
            for token in words:
                if token.operator and token.raw == "|":
                    segments.append([])
                else:
                    segments[-1].append(token)
            outputs: list[str] = []
            parsed: list[list[str]] = []
            for segment in segments:
                argv: list[str] = []
                k = 0
                while k < len(segment):
                    token = segment[k]
                    if token.raw in {"1", "2"} and k + 1 < len(segment) and segment[k + 1].operator and segment[k + 1].raw in {">", ">>", ">&"}:
                        k += 1
                        token = segment[k]
                    if token.operator:
                        if token.raw not in {">", ">>", ">&", "&>"} or k + 1 >= len(segment) or segment[k + 1].operator:
                            raise ValueError("输出重定向缺少目标，或包含不支持的 shell 操作符")
                        target = segment[k + 1].value
                        if token.raw == ">&":
                            if target not in {"1", "2"}:
                                raise ValueError("文件描述符合并只支持 1 或 2")
                        else:
                            outputs.append(target)
                        k += 2
                        continue
                    # Runtime log paths are fine; runtime executable/goal values are not.
                    if argv and executable(argv[0]) == "tee":
                        argv.append(token.value)
                    else:
                        argv.append(_expand(token, variables))
                    k += 1
                if not argv:
                    raise ValueError("管道缺少可执行命令")
                parsed.append(argv)
            if len(parsed) > 1:
                for filter_argv in parsed[1:]:
                    filter_name = executable(filter_argv[0])
                    valid = filter_name == "tee" and all(not arg.startswith("-") or arg in {"-a", "--append", "-i", "--ignore-interrupts", "--"} for arg in filter_argv[1:])
                    args = filter_argv[1:]
                    valid |= filter_name in {"head", "tail"} and (not args or (len(args) == 1 and re.fullmatch(r"-(?:\d+|[nc][+-]?\d+)", args[0]) is not None) or (len(args) == 2 and args[0] in {"-n", "-c"} and re.fullmatch(r"[+-]?\d+", args[1]) is not None))
                    if not valid:
                        raise ValueError(f"管道程序 {filter_name} 不是受支持的纯输出过滤器；不能继承 Maven 编译权限")
                    if filter_name == "tee":
                        outputs.extend(arg for arg in filter_argv[1:] if not arg.startswith("-"))
            for base in sorted(directories):
                result.invocations.append(Invocation(parsed[0], base, script.count("\n", 0, offset) + 1, index, redacted_statement(raw), outputs))
        except (ValueError, IndexError) as exc:
            issue(str(exc), offset, index, raw)
        finally:
            # && can skip a later cd/assignment. After ; or newline, execution
            # resumes in *either* directory. Do not authorize only the last cd.
            if following == "&&":
                skipped_directories.update(before_directories)
                skipped_variables.append(before_variables)
            elif following in {";", "\n", ""}:
                directories.update(skipped_directories)
                for prior in skipped_variables:
                    for key in set(variables) | set(prior):
                        if variables.get(key) != prior.get(key):
                            variables[key] = None
                variables["PWD"] = directory if len(directories) == 1 else None
                skipped_directories.clear()
                skipped_variables.clear()
    return result
