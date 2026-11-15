"""Small lexical helpers for bounded local Java call-chain analysis."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Keep literal values while ignoring comments/formatting in implementation fingerprints.
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[^\s]')
MODIFIERS = re.compile(r'\b(?:public|protected|private|static|final|abstract|synchronized|default|native|strictfp)\b')


def semantic_text(text):
    return '\x1f'.join(m[0] for m in TOKENS.finditer(text) if not m[0].startswith(('//', '/*')))


def end_block(masked, start, opening='(', closing=')'):
    depth = 0
    for i in range(start, len(masked)):
        if masked[i] == opening:
            depth += 1
        elif masked[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(masked)


def arity(masked, raw):
    if not raw.strip():
        return 0
    depth = 0
    count = 1
    for c in masked:
        if c in '([{<':
            depth += 1
        elif c in ')]}>':
            depth -= 1
        elif c == ',' and depth == 0:
            count += 1
    return count


def split_arguments(masked, raw):
    result, start, depth = [], 0, 0
    for i, char in enumerate(masked):
        if char in '([{<':
            depth += 1
        elif char in ')]}>':
            depth -= 1
        elif char == ',' and depth == 0:
            result.append(raw[start:i].strip())
            start = i + 1
    if raw.strip():
        result.append(raw[start:].strip())
    return result


def parameters(masked):
    result = []
    for parameter in split_arguments(masked, masked):
        while '@' in parameter:
            annotation = re.search(r'@[\w.]+\s*', parameter)
            if not annotation:
                break
            end = annotation.end()
            if end < len(parameter) and parameter[end] == '(':
                end = end_block(parameter, end)
            parameter = parameter[:annotation.start()] + parameter[end:]
        while re.search(r'<[^<>]*>', parameter):
            parameter = re.sub(r'<[^<>]*>', '', parameter)
        tokens = re.findall(r'[\w$]+', parameter)
        if len(tokens) >= 2:
            result.append((tokens[-2], tokens[-1]))
    return tuple(result)


@dataclass
class Method:
    name: str
    begin: int
    signature_end: int
    body_start: int
    finish: int
    ending: str
    count: int
    returns: str | None
    annotation_begin: int
    parameters: tuple

    def start(self):
        return self.begin

    def end(self):
        return self.body_start + 1

    def __getitem__(self, key):
        return self.name if key == 1 else self.ending


def members(text, masked, class_start):
    """Inspect direct class members, including implicit-public interface methods.

    Balanced annotations/parameters and bodies keep invocations, constructors and
    nested classes from masquerading as methods. Java is never executed.
    """
    methods, fields = [], []
    opening = masked.find('{', class_start)
    if opening < 0:
        return methods, fields
    cursor = opening + 1
    while cursor < len(masked) and masked[cursor] != '}':
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        annotation_start = cursor
        while cursor < len(masked) and masked[cursor] == '@':
            annotation = re.match(r'@[\w.]+\s*', masked[cursor:])
            if not annotation:
                break
            cursor += annotation.end()
            if cursor < len(masked) and masked[cursor] == '(':
                cursor = end_block(masked, cursor)
            while cursor < len(masked) and masked[cursor].isspace():
                cursor += 1
        begin = cursor
        consumed = False
        while cursor < len(masked):
            char = masked[cursor]
            if char == '}':
                return methods, fields
            if char == '(':
                prefix = masked[begin:cursor].strip()
                name = re.search(r'([\w$]+)\s*$', prefix)
                before = MODIFIERS.sub('', prefix[:name.start()]).strip() if name else ''
                close = end_block(masked, cursor)
                after = close
                while after < len(masked) and masked[after] not in ';{}=':
                    after += 1
                # Prefix must contain a return type, not an initializer/constructor.
                if (name and before and '=' not in prefix and not re.search(r'\b(class|interface|record|enum|new)\b', prefix)
                        and after < len(masked) and masked[after] in ';{'):
                    ending = masked[after]
                    finish = end_block(masked, after, '{', '}') if ending == '{' else after + 1
                    return_names = re.findall(r'\b[A-Z]\w*\b', before.split('<', 1)[0])
                    methods.append(Method(name[1], begin, close, after, finish, ending,
                                          arity(masked[cursor + 1:close - 1], text[cursor + 1:close - 1]),
                                          return_names[-1] if return_names else None, annotation_start,
                                          parameters(masked[cursor + 1:close - 1])))
                    cursor, consumed = finish, True
                    break
                cursor = close
                continue
            if char == '{':
                cursor = end_block(masked, cursor, '{', '}')
                # Field object/array initializers continue to their semicolon.
                if '=' in masked[begin:cursor].split('{', 1)[0]:
                    continue
                consumed = True
                break
            if char == ';':
                field = masked[begin:cursor]
                declaration = re.match(r'(?:[\w.<>?,\[\]]+\s+)+([\w$]+)\s*(?:=|$)', field.strip())
                if declaration and not re.search(r'\b(package|import|return)\b', field):
                    fields.append((declaration[1], annotation_start, cursor + 1))
                cursor += 1
                consumed = True
                break
            cursor += 1
        if not consumed:
            break
    return methods, fields


def enum_constants(masked, class_start):
    if not re.match(r'enum\b', masked[class_start:]):
        return []
    result = []
    cursor = masked.find('{', class_start) + 1
    while cursor > 0 and cursor < len(masked):
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        name = re.match(r'[A-Za-z_$][\w$]*', masked[cursor:])
        if not name:
            break
        begin, identifier = cursor, name[0]
        cursor += name.end()
        while cursor < len(masked) and masked[cursor] not in ',;}':
            if masked[cursor] in '({':
                char = masked[cursor]
                cursor = end_block(masked, cursor, char, ')' if char == '(' else '}')
            else:
                cursor += 1
        result.append((identifier, begin, cursor))
        if cursor >= len(masked) or masked[cursor] != ',':
            break
        cursor += 1
    return result
