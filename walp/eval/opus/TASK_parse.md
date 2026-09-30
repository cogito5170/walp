# Task A — command → goal (files: heldout_corpus.inputs.txt, novel_corpus.inputs.txt)

A robot searches a grid world (4 rooms) for ONE object. Each input line is `<id>\t<user command>` (English or Korean).
Output one JSON per line: {"id":N,"status":"OK"|"ASK"|"REJECT","goal":{...}} (goal only when OK).

Goal fields (use only these values):
- type (required): card | key | cup | box
- color: red | blue | green | black
- brand: visa | master   (brands exist only on cards)
- avoid: counter | desk | floor | shelf   (a zone the robot must stay out of)
- deadline: integer step limit
- uncertain: observe   (user asked to look again when unsure)
- blocked: replan      (user asked to replan when the path is blocked)
Omit fields the user did not specify.

Status:
- OK: the command asks to FIND/LOCATE one object and every stated constraint maps exactly onto the fields above.
- ASK: a person would need to ask back because it is genuinely ambiguous between supported values.
- REJECT: anything else — a different action (bring, pick up, clean, go to…), an unsupported object/color/brand/zone,
  constraints that cannot be expressed (negations like "not blue", "except …"), contradictions, or no identifiable target.
Do not guess: if you are not sure a word means a supported value, choose ASK or REJECT rather than OK.
