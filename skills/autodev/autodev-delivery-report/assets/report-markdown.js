"use strict";
// Parse only document content; raw HTML and resource fetching stay disabled.
const ReportMarkdown = (() => {
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const renderer = new marked.Renderer();
  renderer.html = ({ text }) =>
    /^\s*<!--[\s\S]*?-->\s*$/.test(text)
      ? ""
      : /^\s*<\/?(?:br|kbd|sup|sub)\s*\/?>\s*$/i.test(text)
        ? text
        : escape(text);
  renderer.image = ({ href, title, text }) =>
    '<span class="md-image-ref" title="' +
    escape(href) +
    '">图片：' +
    escape(text || title || href) +
    " <small>（离线报告未内嵌此资源）</small></span>";
  const parser = new marked.Marked({ gfm: true, breaks: false, async: false, renderer });
  const config = {
    ALLOWED_TAGS: [
      "a",
      "p",
      "br",
      "hr",
      "strong",
      "em",
      "del",
      "h1",
      "h2",
      "h3",
      "h4",
      "h5",
      "h6",
      "blockquote",
      "pre",
      "code",
      "ul",
      "ol",
      "li",
      "table",
      "thead",
      "tbody",
      "tr",
      "th",
      "td",
      "input",
      "span",
      "kbd",
      "sup",
      "sub",
    ],
    ALLOWED_ATTR: ["href", "title", "align", "start", "type", "disabled", "checked", "class"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  };
  const slug = (text) =>
    String(text)
      .normalize("NFKC")
      .trim()
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s_-]/gu, "")
      .replace(/\s+/g, "-") || "section";
  function decode(value) {
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  function resolvePath(from, target) {
    const name = String(target).replace(/\\/g, "/");
    if (/^[a-z][a-z\d+.-]*:/i.test(name) || name.startsWith("/") || /[\x00-\x1f\x7f]/.test(name)) return null;
    const parts = String(from ?? "")
      .split("/")
      .slice(0, -1);
    for (const part of name.split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") {
        if (!parts.length) return null;
        parts.pop();
      } else parts.push(part);
    }
    return parts.join("/");
  }
  let renderSequence = 0;
  function render(value, { scope = "document-" + ++renderSequence, path = "" } = {}) {
    const text = String(value ?? "");
    if (!text.trim()) return '<p class="md-empty">0 · 暂无文档内容</p>';
    let body = text,
      meta = "";
    const frontmatter = /^\uFEFF?---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)/.exec(body);
    if (frontmatter && /^\s*[^\s:]+\s*:/m.test(frontmatter[1])) {
      meta =
        '<details class="md-frontmatter"><summary>文档元信息</summary><pre><code>' +
        escape(frontmatter[1]) +
        "</code></pre></details>";
      body = body.slice(frontmatter[0].length);
    }
    const fragment = DOMPurify.sanitize(parser.parse(body), config);
    const prefix = "md-" + slug(scope) + "-";
    const seen = new Map();
    for (const heading of fragment.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
      const base = slug(heading.textContent),
        count = seen.get(base) ?? 0;
      heading.id = prefix + base + (count ? "-" + count : "");
      seen.set(base, count + 1);
      heading.dataset.mdSlug = base + (count ? "-" + count : "");
    }
    for (const element of fragment.querySelectorAll("[class]")) {
      const language =
        element.tagName === "CODE"
          ? Array.from(element.classList).find((name) => /^language-[\w+-]{1,40}$/.test(name))
          : null;
      const image = element.tagName === "SPAN" && element.classList.contains("md-image-ref");
      element.removeAttribute("class");
      if (language) element.className = language;
      if (image) element.className = "md-image-ref";
    }
    for (const cell of fragment.querySelectorAll("[align]")) {
      const align = cell.getAttribute("align");
      cell.removeAttribute("align");
      if (["left", "center", "right"].includes(align)) cell.classList.add("md-align-" + align);
    }
    for (const checkbox of fragment.querySelectorAll("input")) {
      if (checkbox.getAttribute("type") !== "checkbox") {
        checkbox.remove();
        continue;
      }
      checkbox.disabled = true;
      checkbox.setAttribute("aria-label", checkbox.checked ? "已完成" : "未完成");
      checkbox.closest("li")?.classList.add("md-task-item");
    }
    for (const link of fragment.querySelectorAll("a")) {
      const href = (link.getAttribute("href") ?? "").trim();
      link.removeAttribute("href");
      if (/^(?:https?:|mailto:)/i.test(href)) {
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      } else if (href.startsWith("#")) {
        const hash = decode(href.slice(1));
        link.href = "#" + prefix + slug(hash);
        link.dataset.mdAnchor = prefix + slug(hash);
        link.dataset.mdHash = hash;
        link.dataset.mdFile = path;
      } else if (href && !/^[a-z][a-z\d+.-]*:/i.test(href) && !href.startsWith("//")) {
        const [file, hash = ""] = href.split("#");
        const resolved = resolvePath(path, decode(file));
        if (resolved && /\.md$/i.test(resolved)) {
          link.href = "#";
          link.dataset.mdFile = resolved;
          link.dataset.mdHash = decode(hash);
        } else {
          link.title = "原文链接：" + href;
          link.classList.add("md-link-unresolved");
        }
      }
    }
    for (const table of fragment.querySelectorAll("table")) {
      const wrapper = document.createElement("div");
      wrapper.className = "md-table-scroll";
      wrapper.tabIndex = 0;
      wrapper.setAttribute("aria-label", "Markdown 表格");
      table.before(wrapper);
      wrapper.appendChild(table);
    }
    for (const code of fragment.querySelectorAll("pre>code")) {
      const language = Array.from(code.classList).find((name) => name.startsWith("language-"));
      if (language) code.parentElement.dataset.language = language.slice(9);
    }
    const wrapper = document.createElement("div");
    wrapper.append(fragment);
    return meta + wrapper.innerHTML;
  }
  return Object.freeze({ render, slug, resolvePath });
})();
