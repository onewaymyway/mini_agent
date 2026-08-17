import { diffLines } from "diff";
import { Empty } from "antd";

/**
 * 轻量 Diff 视图，对应旧看板 `diff_view.py`，被"目标执行规范修订对比"
 * "进化提案 diff"等多处复用。用 `diff` 包做行级 diff，自行渲染，
 * 避免引入较重的 react-diff-viewer + syntax-highlighter 依赖。
 */
export default function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  if (!oldText && !newText) return <Empty description="无内容" />;
  const parts = diffLines(oldText || "", newText || "");

  return (
    <pre
      style={{
        fontFamily: "monospace",
        fontSize: 12,
        lineHeight: 1.6,
        background: "#fafafa",
        border: "1px solid #f0f0f0",
        borderRadius: 6,
        padding: 12,
        maxHeight: 480,
        overflow: "auto",
        margin: 0,
      }}
    >
      {parts.map((part, idx) => {
        const color = part.added ? "#e6ffed" : part.removed ? "#ffeef0" : "transparent";
        const prefix = part.added ? "+ " : part.removed ? "- " : "  ";
        const textColor = part.added ? "#22863a" : part.removed ? "#b31d28" : undefined;
        return (
          <div key={idx} style={{ background: color, color: textColor, whiteSpace: "pre-wrap" }}>
            {part.value
              .replace(/\n$/, "")
              .split("\n")
              .map((line, i) => (
                <div key={i}>
                  {prefix}
                  {line}
                </div>
              ))}
          </div>
        );
      })}
    </pre>
  );
}
