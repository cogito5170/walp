# Task B — request → tool call (files: tool_corpus_v2.inputs.txt, tool_corpus_v3.inputs.txt; tool list: tools.md)

You are the tool router of an engineering assistant. tools.md lists every tool: name(params: type = default) and its
description. Each input line is `<id>\t<user request>` (Korean/English, may contain typos or abbreviations).
Output one JSON per line: {"id":N,"status":"TOOL"|"ASK"|"REJECT","tool":name,"args":{param:value}}.
- TOOL: exactly one tool clearly fits AND every required parameter (no default) can be taken from the request.
  Fill args with values stated in the request (use the parameter names from tools.md; for code/markdown params put the text).
- ASK: a tool fits but a required value is missing, or two tools fit about equally.
- REJECT: no listed tool fits.
Running the wrong tool is the worst outcome; asking is cheaper.
