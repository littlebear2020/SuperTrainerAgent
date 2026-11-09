"use client";

import { Button } from "@/components/ui/button";
import { submitThroughComposer } from "@/core/messages/composer";

/**
 * Clickable choices for an `ask_clarification` tool call (plan P1-12).
 *
 * The backend already ships `options` in the tool-call args — 从零创作 has been
 * consuming them all along — but the main chat only showed a static
 * 「需要你的协助」 line, leaving the user to retype the option by hand.
 */
export function ClarificationOptions({
  options,
  disabled = false,
}: {
  options: string[];
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col items-start gap-1.5">
      {options.map((option) => (
        <Button
          key={option}
          className="h-auto w-fit max-w-full justify-start rounded-md px-2.5 py-1.5 text-left leading-5 whitespace-normal"
          disabled={disabled}
          size="sm"
          type="button"
          variant="outline"
          onClick={() => submitThroughComposer(option)}
        >
          {option}
        </Button>
      ))}
    </div>
  );
}
