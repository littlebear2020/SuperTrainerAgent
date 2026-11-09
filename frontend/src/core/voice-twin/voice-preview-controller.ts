/**
 * Plain (non-React) controller for single-audio voice preview playback.
 *
 * Extracted from the hook so the tricky logic is unit-testable in the node
 * vitest environment WITHOUT @testing-library/react (this project deliberately
 * does not install it — see tests/unit/core/writer/hooks.test.ts). The hook
 * `useVoicePreview` is a thin React wrapper.
 *
 * One Audio() instance is reused; at most one voice plays at a time. Toggling
 * the playing voice pauses it; switching voices stops the previous and starts
 * the new. Mirrors video-gen's `togglePreview`.
 */
export interface VoicePreviewController {
  /** Toggle a voice's preview. Returns the resulting playingId (null = stopped). */
  toggle: (voiceId: string, url: string) => string | null;
  getPlayingId: () => string | null;
  /** Voice currently buffering; null once playback starts or on error. */
  getLoadingId: () => string | null;
  /** Stop everything and release the audio element. */
  dispose: () => void;
}

export function createVoicePreviewController(opts?: {
  onChange?: () => void;
}): VoicePreviewController {
  let audio: HTMLAudioElement | null = null;
  let playingId: string | null = null;
  let loadingId: string | null = null;

  const notify = () => opts?.onChange?.();

  const ensureAudio = (): HTMLAudioElement => {
    if (!audio) {
      audio = new Audio();
      audio.onended = () => {
        playingId = null;
        loadingId = null;
        notify();
      };
      audio.onerror = () => {
        playingId = null;
        loadingId = null;
        notify();
      };
    }
    return audio;
  };

  const toggle = (voiceId: string, url: string): string | null => {
    const el = ensureAudio();

    if (playingId === voiceId) {
      el.pause();
      playingId = null;
      loadingId = null;
      notify();
      return null;
    }

    el.pause(); // stop whatever was playing
    el.src = url;
    loadingId = voiceId;
    // Capture the voice this play() call is for, so a delayed .then/.catch
    // (e.g. the AbortError a browser raises when pause()/src-change interrupts
    // an unsettled play()) cannot clobber a voice the user has since switched to.
    const startedId = voiceId;
    void el
      .play()
      .then(() => {
        if (loadingId === startedId) {
          loadingId = null;
          notify();
        }
      })
      .catch(() => {
        if (playingId === startedId) {
          playingId = null;
          loadingId = null;
          notify();
        }
      });
    playingId = voiceId;
    notify();
    return voiceId;
  };

  return {
    toggle,
    getPlayingId: () => playingId,
    getLoadingId: () => loadingId,
    dispose: () => {
      audio?.pause();
      audio = null;
      playingId = null;
      loadingId = null;
      notify();
    },
  };
}
