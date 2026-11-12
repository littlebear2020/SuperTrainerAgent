/**
 * Fill the chat composer with `text` and submit it, i.e. exactly what happens
 * when the user types the text and presses Enter.
 *
 * The composer is mounted outside most card subtrees (InputBox on the chat
 * pages, a bare PromptInput on 新建智能体), so it is reached through the DOM
 * the same way input-box.tsx's SuggestionList reaches it.
 *
 * Shared by ClarificationOptions (plan P1-12) and the empty-response
 * fallback card (2026-09-23 plan B2). Returns false when no composer is
 * mounted, so the caller can leave the action un-submitted rather than
 * pretend it was sent.
 */
export function submitThroughComposer(text: string): boolean {
  const textarea = document.querySelector<HTMLTextAreaElement>(
    "textarea[name='message']",
  );
  if (!textarea) {
    return false;
  }
  // React installs its own `value` setter on the mounted textarea, and it
  // swallows writes it believes are no-ops, so `textarea.value = text` leaves
  // the controlled composer's state empty and submits "". Writing through the
  // prototype setter bypasses that shadowing; the `input` event then lets both
  // the controlled and the uncontrolled composer pick the value up.
  Reflect.set(HTMLTextAreaElement.prototype, "value", text, textarea);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  // Submit on the next tick so the controlled input flushes before form submission.
  // so React has flushed the value into state first.
  setTimeout(() => textarea.form?.requestSubmit(), 0);
  return true;
}
