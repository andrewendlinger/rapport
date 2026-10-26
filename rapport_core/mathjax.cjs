// TeX -> SVG with MathJax 3, for rapport's math modules. Reads JSON
// {"tex": "...", "display": true} on stdin, prints the <svg> element.
// fontCache 'none' keeps every SVG self-contained (no shared ids between formulas).
const { mathjax } = require("mathjax-full/js/mathjax.js");
const { TeX } = require("mathjax-full/js/input/tex.js");
const { SVG } = require("mathjax-full/js/output/svg.js");
const { liteAdaptor } = require("mathjax-full/js/adaptors/liteAdaptor.js");
const { RegisterHTMLHandler } = require("mathjax-full/js/handlers/html.js");
const { AllPackages } = require("mathjax-full/js/input/tex/AllPackages.js");

let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const { tex, display } = JSON.parse(input);
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const doc = mathjax.document("", {
    InputJax: new TeX({ packages: AllPackages.filter((p) => p !== "bussproofs") }),
    OutputJax: new SVG({ fontCache: "none" }),
  });
  const node = doc.convert(tex, { display: !!display });
  const svg = adaptor.innerHTML(node);
  const err = svg.match(/data-mjx-error="([^"]*)"/);
  if (err) {
    process.stderr.write("TeX error: " + err[1] + "\n");
    process.exit(2);
  }
  process.stdout.write(svg);
});
