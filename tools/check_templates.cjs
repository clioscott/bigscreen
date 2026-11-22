// The control panel's Vue templates, compiled the way the browser does at runtime (parse, generate, new Function).
// A template that fails takes its whole page down to a blank screen, and `node --check` can't see it: the template
// is a string. Usage: node tools/check_templates.cjs <files...> (needs @vue/compiler-dom, the panel's Vue version).
const fs = require("fs");
const { compile } = require("@vue/compiler-dom");
let bad = 0, n = 0;
for (const file of process.argv.slice(2)) {
  const src = fs.readFileSync(file, "utf8");
  for (const m of src.matchAll(/template: `((?:[^`\\]|\\.)*)`/g)) {
    n++;
    const t = new Function("return `" + m[1] + "`")();
    const errs = [];
    try {
      const { code } = compile(t, { onError: (e) => errs.push(e.message), hoistStatic: true });
      new Function("Vue", code);
    } catch (e) { errs.push(e.message); }
    if (errs.length) { bad++; console.log("BAD", file, ":", errs.join("; "), "::", t.slice(0, 80).replace(/\n/g, " ")); }
  }
}
console.log(n, "templates,", bad, "bad");
process.exit(bad ? 1 : 0);
