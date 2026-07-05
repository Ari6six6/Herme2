You called finish_run on a build, but you changed code and never once queried the
twin to check it. That is exactly the move you do not get to make: declaring "it
works" without showing it.

"It works" is true only when you ran it and saw the right output — never because
you said so. So before you finish:

1. Pick a real input the twin covers (`twin_map` if you need to see the surface).
2. Run YOUR solution on that input and capture its real output.
3. Get the twin's real response for the same input with `twin_request`.
4. Compare them. If they differ, it does not work — fix the code and repeat. If
   they match, finish and quote both outputs as your proof.

An independent antithesis pass will try to break your solution against the twin
after you finish. Fabricated success does not survive it. Do not finish again
until you have proof you ran it, not a claim that you did.
