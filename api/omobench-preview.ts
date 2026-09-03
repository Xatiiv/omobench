import worker from "../dist/server/index.js";

type WorkerContext = {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
};

const emptyEnvironment = Object.freeze({});

export default {
  async fetch(request: Request): Promise<Response> {
    const context: WorkerContext = {
      waitUntil(promise) {
        void promise.catch((error: unknown) => {
          console.error("Deferred OmoBench work failed:", error);
        });
      },
      passThroughOnException() {
        // Vercel handles uncaught errors itself.
      },
    };

    return worker.fetch(request, emptyEnvironment, context);
  },
};
