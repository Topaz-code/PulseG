import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/**
 * Renders project Markdown - the design document, the story, task output, test reports.
 *
 * GitHub-flavoured Markdown because the project files use tables and task lists, and a plain
 * renderer turns those into unreadable run-on paragraphs. `skipHtml` is deliberate: these files
 * can contain agent output, and nothing from a model should be able to inject markup.
 */
export function MarkdownView({ content, className }: { content: string; className?: string }) {
  if (!content?.trim()) {
    return (
      <p className={cn("text-sm text-muted italic", className)}>
        Nothing written here yet. It appears as soon as the team produces it.
      </p>
    );
  }
  return (
    <div className={cn("prose-pulse max-w-none", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
