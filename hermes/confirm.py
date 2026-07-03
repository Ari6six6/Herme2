"""The single confirmation chokepoint.

Every gated action goes through confirm(): it prints exactly what is about
to happen and waits for y/n. An optional `viewable` payload (e.g. forged
tool source) can be inspected with 'v' before deciding.
"""

from __future__ import annotations

from hermes.ui import bold, dim, yellow


def confirm(action: str, detail: str = "", viewable: str | None = None) -> bool:
    print(f"\n{bold(yellow('[confirm]'))} {bold(action)}")
    if detail:
        print(detail)
    options = "[y/n/v]" if viewable is not None else "[y/N]"
    while True:
        try:
            answer = input(yellow(f"Allow? {options} ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(dim("\n(denied)"))
            return False
        if answer == "v" and viewable is not None:
            print(dim("---- source ----"))
            print(viewable)
            print(dim("---- end ----"))
            continue
        return answer in ("y", "yes")
