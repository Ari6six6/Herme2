Read documents: turn a downloaded HTML page or PDF into plain text you can answer from.

http_request and download_file can bring content in, but they hand you raw HTML
(token-thick noise) or binary PDF bytes. These two toolbox tools turn "fetched"
into "readable". Both are LOCAL — they transform a file already in the project;
neither fetches, so fetch FIRST, then extract.

Web pages:
1. equip_tool("html_to_text"), then download the page to a file so its raw HTML
   never floods your context:
   - download_file(url="https://…", dest="workspace/page.html")
   - html_to_text(src="workspace/page.html")     # scripts/styles/tags stripped
   You can also pass inline text= if you already have the HTML in hand, and
   dest= to save the cleaned text instead of returning it.

PDFs:
1. equip_tool("pdf_text"), then:
   - download_file(url="https://…/paper.pdf", dest="workspace/paper.pdf")
   - pdf_text(src="workspace/paper.pdf")           # whole document
   - pdf_text(src="workspace/paper.pdf", pages="2-5")   # scope big PDFs
   Save huge extractions with dest="workspace/paper.txt" rather than dumping
   thousands of chars into the run.

Gotchas learned the hard way:
- Neither tool takes a url — pass a src file or inline text. Fetch with
  download_file/http_request first (that's also where the taint rail applies).
- pdf_text needs pypdf; if it's missing it returns a clear "pip install pypdf".
- A scanned PDF (images, no text layer) extracts nothing — pdf_text does not OCR.
- Prefer download_file(dest=...) over http_request for documents: it keeps the
  raw bytes out of your context window until you've distilled them.
