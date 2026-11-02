import "@testing-library/jest-dom/vitest";

// jsdom lacks ResizeObserver (every supported browser has it); charts only need it to exist in tests.
if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver = ResizeObserverStub;
}

// jsdom has no animation frames unless "pretendToBeVisual"; playback only needs them to tick.
if (typeof globalThis.requestAnimationFrame !== "function") {
  globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 16) as unknown as number;
  globalThis.cancelAnimationFrame = (id: number) => clearTimeout(id);
}
