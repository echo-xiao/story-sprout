import { useRef, useEffect, type TextareaHTMLAttributes } from "react";

/** Auto-resizing textarea that grows with content */
export default function AutoTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const resize = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = el.scrollHeight + "px";
  };
  useEffect(resize, [props.value]);
  return <textarea ref={ref} {...props} onInput={resize} style={{ ...props.style, overflow: "hidden" }} />;
}
