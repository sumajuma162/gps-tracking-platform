import type { ReactNode } from "react";

/**
 * Minimal, safe Markdown for setup instructions: headings, ordered/unordered lists, paragraphs,
 * **bold** and `code`. Produces React elements only (never raw HTML), so catalog text cannot inject markup.
 */
function inline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i} className="rounded bg-paper px-1 py-0.5 text-[0.9em]">{p.slice(1, -1)}</code>;
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
    return p;
  });
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const Tag = (["h3", "h4", "h5"] as const)[heading[1].length - 1];
      blocks.push(<Tag key={i} className="mt-3 font-display text-lg font-bold first:mt-0">{inline(heading[2])}</Tag>);
      i += 1;
      continue;
    }
    const ordered = /^\d+\.\s+/.test(line);
    if (ordered || /^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && (ordered ? /^\d+\.\s+/ : /^[-*]\s+/).test(lines[i])) {
        items.push(lines[i].replace(ordered ? /^\d+\.\s+/ : /^[-*]\s+/, ""));
        i += 1;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={i} className={`my-2 space-y-1 pl-6 ${ordered ? "list-decimal" : "list-disc"}`}>{items.map((it, k) => <li key={k}>{inline(it)}</li>)}</List>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\d+\.\s|[-*]\s)/.test(lines[i])) { para.push(lines[i]); i += 1; }
    blocks.push(<p key={i} className="my-2">{inline(para.join(" "))}</p>);
  }
  return <div className="text-sm leading-relaxed">{blocks}</div>;
}
