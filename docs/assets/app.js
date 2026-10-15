// Shared helpers. Plain browser JS, no dependencies, no network except
// same-origin GETs of the committed JSON under data/.
"use strict";

const REPO = "https://github.com/Pranav240/paper-trading-agent";
const FNSPID = "https://github.com/Zdong104/FNSPID_Financial_News_Dataset";

// Defence in depth: the exported JSON is already cut; never show more than 100 characters.
const cut = (t) => (t.length <= 100 ? t : t.slice(0, 100).trimEnd() + "…");

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(path + ": HTTP " + res.status);
  return res.json();
}

function showError(target, err) {
  target.replaceChildren(el("div", { class: "banner" },
    el("strong", { text: "Could not load the data. " }),
    "This page reads JSON over HTTP, so it does not work when opened as a local file. ",
    "Serve the docs folder (python -m http.server -d docs) or use the published site. ",
    el("span", { class: "muted", text: String(err.message || err) })));
}

const fmt = {
  money(x) { return (x < 0 ? "−$" : "+$") + Math.abs(x).toFixed(2); },
  plain(x) { return "$" + x.toFixed(2); },
  pct(x, d = 1) { return (x * 100).toFixed(d) + "%"; },
  p(x) { return x < 0.0001 ? "p < 0.0001" : "p = " + x.toFixed(4); },
};

function nav(current) {
  const items = [["index.html", "Home"], ["decisions.html", "Decisions"],
                 ["evaluation.html", "Evaluation"], ["failures.html", "Known failures"]];
  const header = el("header", { class: "site" }, el("div", { class: "wrap" },
    el("a", { class: "brand", href: "index.html", text: "Paper Trading Agent: static demo" }),
    el("nav", { "aria-label": "Main" }, items.map(([href, label]) =>
      el("a", { href, "aria-current": href === current ? "page" : false, text: label })))));
  document.body.prepend(header);
}

function footer() {
  document.querySelector("main").append(el("footer", {},
    "A static demo of recorded decisions. Paper trading only; nothing here is financial advice. ",
    "No live model is called and the site only reads files. ",
    el("a", { href: REPO, text: "Source repository" }), ". ",
    el("br"), "News headlines come from the ",
    el("a", { href: FNSPID, text: "FNSPID dataset" }),
    " (Dong et al., ", el("a", { href: "https://arxiv.org/abs/2402.06698", text: "arXiv:2402.06698" }),
    ") and are cut to their first 100 characters; the full text is in the dataset."));
}

const actionTag = (a) => el("span", { class: "tag " + (a === "BUY" ? "ok" : a === "SELL" ? "bad" : ""), text: a });
const effectTag = (e) => e ? el("span", { class: "tag warn", text: e }) : el("span", { class: "muted", text: "none" });
