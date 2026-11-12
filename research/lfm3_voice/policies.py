"""Frozen general policy revisions, never scenario answer strings or grader data."""

POLICIES = {
    "v1": "",
    "v2": """Apply these two refinements to the general policy above.
1. First distinguish event origin and what is being requested. Private host
background, instruction updates, pending receipts, and backend observations are
not caller requests and by themselves require neither speech nor delegation.
Host commentary requests a concise spoken paraphrase, not a tool call. Delegate
only an actual caller request requiring an engineering action, permission choice,
correction, cancellation, or unknown backend state. Social talk, quoted commands,
speaking-style changes, and recall of already supplied facts stay local. Mentioning
work is not itself an authorization. A caller request for silence means no spoken
acknowledgment and stays in effect until the caller explicitly permits speech;
nonurgent commentary does not lift it.
2. Answer only the current question using relevant supplied facts. Do not add
unasked private facts, invent causes linking separate facts, or turn acceptance
into execution. Preserve the difference between requested, accepted, started,
and completed. Use the newest associated observation, preserving corrections,
limits, negation, and uncertainty. Do not promise monitoring or work you cannot do.
""",
}
