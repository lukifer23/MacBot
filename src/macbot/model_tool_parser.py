"""Parse model function syntax as data. No evaluation of callable expressions."""

import ast
import json
import re


def parse_calls(text: str) -> list[dict]:
    if len(text) > 20000:
        raise ValueError("Tool output exceeds limit")
    if "<tool_call>" in text:
        bodies = re.findall(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
        if not 1 <= len(bodies) <= 4 or len(bodies) != text.count("<tool_call>"):
            raise ValueError("Incomplete or excessive tool calls")
        calls = []
        for body in bodies:
            if body.strip().startswith("{"):
                obj = json.loads(body)
                if (
                    not isinstance(obj, dict)
                    or not isinstance(obj.get("name"), str)
                    or not isinstance(obj.get("arguments"), dict)
                ):
                    raise ValueError("Invalid JSON tool call")
            else:
                function = re.fullmatch(
                    r"\s*<function=([A-Za-z_]\w*)>(.*?)</function>\s*", body, re.DOTALL
                )
                if not function:
                    raise ValueError("Invalid XML-style function call")
                parameters = function[2]
                arguments = {}
                pattern = re.compile(r"\s*<parameter=([A-Za-z_]\w*)>(.*?)</parameter>", re.DOTALL)
                position = 0
                while parameters[position:].strip():
                    match = pattern.match(parameters, position)
                    if not match or match[1] in arguments:
                        raise ValueError("Invalid or duplicate tool parameter")
                    arguments[match[1]] = match[2].strip()
                    position = match.end()
                obj = {"name": function[1], "arguments": arguments}
            calls.append({"name": obj["name"], "arguments": json.dumps(obj["arguments"])})
        return calls
    if "<|tool_call_start|>" not in text or "<|tool_call_end|>" not in text:
        raise ValueError("Incomplete tool call markers")
    body = text.split("<|tool_call_start|>", 1)[1].split("<|tool_call_end|>", 1)[0]
    tree = ast.parse(body, mode="eval").body
    if not isinstance(tree, ast.List) or not 1 <= len(tree.elts) <= 4:
        raise ValueError("Expected a bounded list of tool calls")
    calls = []
    for expression in tree.elts:
        if (
            not isinstance(expression, ast.Call)
            or not isinstance(expression.func, ast.Name)
            or expression.args
        ):
            raise ValueError("Only named tools with keyword arguments are accepted")
        arguments = {}
        for keyword in expression.keywords:
            if (
                keyword.arg is None
                or keyword.arg in arguments
                or not isinstance(keyword.value, ast.Constant)
                or not isinstance(keyword.value.value, str)
            ):
                raise ValueError("Only literal string arguments are accepted")
            arguments[keyword.arg] = keyword.value.value
        calls.append({"name": expression.func.id, "arguments": json.dumps(arguments)})
    return calls
