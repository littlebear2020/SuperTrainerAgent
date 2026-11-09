/**
 * HTML artifact preview helpers.
 *
 * The preview renders agent-produced HTML inside an iframe fed by a `blob:`
 * object URL. Blob URLs have an opaque path, so a relative `src="assets/x.png"`
 * has nothing to resolve against and the browser never issues a request —
 * the preview degrades to a bare skeleton with broken images. Injecting a
 * `<base href>` that points at the artifact's own directory restores them.
 */

/**
 * Inject `<base href="<artifact directory>/">` into previewed HTML.
 *
 * @param content   The artifact's raw HTML.
 * @param url       The artifact's own URL (as served by the artifacts
 *                  endpoint). Relative URLs are resolved against `currentHref`
 *                  because production runs behind nginx with an empty backend
 *                  base URL, and a relative base is useless inside a blob doc.
 * @param currentHref Absolute base used to resolve `url`.
 */
export function appendHtmlPreviewBaseHref(
  content: string,
  url?: string,
  currentHref = globalThis.location?.href ?? "http://localhost/",
) {
  // An author-supplied <base> wins: a second one is ignored by the parser
  // anyway, and overriding it would change how the page was written to resolve.
  if (!url || /<base\s/i.test(content)) {
    return content;
  }

  const baseElement = `<base href="${escapeHtmlAttribute(htmlBaseHref(url, currentHref))}">`;
  if (/<head[^>]*>/i.test(content)) {
    return content.replace(/<head([^>]*)>/i, `<head$1>${baseElement}`);
  }
  // Fragments and documents with no <head> still need the base; prepending it
  // works because the parser hoists it into the implied head.
  return `${baseElement}${content}`;
}

/** Strip the filename, query and hash so the base is the artifact's directory. */
function htmlBaseHref(url: string, currentHref: string) {
  const baseUrl = new URL(url, currentHref);
  baseUrl.pathname = baseUrl.pathname.replace(/\/[^/]*$/, "/");
  baseUrl.search = "";
  baseUrl.hash = "";
  return baseUrl.toString();
}

function escapeHtmlAttribute(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;");
}

/**
 * PR-3 5.2：CSV/TSV 表格预览的定界符判定——null = 非表格化文件（走原
 * code/preview 分支）。csv → ","，tsv → "\t"。
 */
export function getTabularDelimiter(
  language: string | null,
): "," | "\t" | null {
  return language === "csv" ? "," : language === "tsv" ? "\t" : null;
}
