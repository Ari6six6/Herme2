You are ending this run with code in your answer, but you never wrote it to a
file or ran it — no `write_file`, `edit_file`, `remote_*`, `host_*`,
`local_shell`, or toolbox file tool was called this whole run. That means the
code exists only in this message. Nobody runs the code in a chat reply; the
files you named do not exist on disk. That is the one thing this system must
never do.

Choose now, in this turn:

- You were asked to BUILD, FIX, or CREATE something: stop describing it and do
  it. Call `write_file` to put the code in a real file under `workspace/`
  (verify the path with `list_files` first if unsure it exists), then run it
  with `remote_shell` or `local_shell` to confirm it works. Only after the file
  exists and runs should you `finish_run`.
- You were asked ONLY to explain or show an example, and writing a file would
  be wrong: call `finish_run` again as-is. This check won't stop you twice.
