/**
 * Browser APIs jsdom does not implement, stubbed to the minimum the components use.
 *
 * Each one is here because a view genuinely calls it and jsdom has no layout engine: a chat rail
 * scrolls to its newest line, the quota meters observe their own size, and the preview pane asks
 * whether the window prefers reduced motion. Stubbing them keeps the tests honest about what they
 * are checking - that the screen renders and reads correctly - instead of pretending to verify
 * layout that no headless DOM can verify anyway.
 */

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {
    /* no layout in jsdom; the call exists so hooks that follow the tail do not throw */
  };
}

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
