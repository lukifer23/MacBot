'use strict';

// One replay cursor and one in-flight journal request. Live notifications must
// never advance this cursor ahead of the historical replay on a fresh tab.
class MacBotEventFeed {
  constructor(read, consume, failed) {
    this.read = read;
    this.consume = consume;
    this.failed = failed;
    this.cursor = 0;
    this.epoch = '';
    this.controller = null;
  }
  start() {
    if (this.controller) return;
    const controller = new AbortController();
    this.controller = controller;
    this.done = this.run(controller);
  }
  stop() {
    this.controller?.abort();
    this.controller = null;
  }
  async run(controller) {
    const signal = controller.signal;
    while (!signal.aborted) {
      try {
        const batch = await this.read(this.cursor, this.epoch, signal);
        if (signal.aborted) return;
        if (!Array.isArray(batch.events) || !Number.isSafeInteger(batch.cursor) || !batch.epoch) {
          throw new Error('Invalid conversation event response');
        }
        this.consume(batch);
        this.cursor = batch.cursor;
        this.epoch = batch.epoch;
      } catch (error) {
        if (signal.aborted) return;
        this.failed(error);
        if (signal.aborted) return;
        await new Promise(resolve => {
          const abort = () => {clearTimeout(timer); resolve();};
          const timer = setTimeout(() => {signal.removeEventListener('abort', abort); resolve();}, 1000);
          signal.addEventListener('abort', abort, {once: true});
        });
      }
    }
  }
}

if (typeof module !== 'undefined') module.exports = {MacBotEventFeed};
