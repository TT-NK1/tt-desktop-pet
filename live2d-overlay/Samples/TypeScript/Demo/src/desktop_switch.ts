import { StreetPose } from './street_poses';

export const transitionTime = {outfit: .300, action: .200};
const bodies = ['gothic', 'maid', 'bunny', 'sailor'];
const actions: Record<string,string[]> = {
  gothic: ['tea', 'handcraft'], maid: ['serving', 'cleaning'],
  bunny: ['hug', 'stretch'], sailor: ['phone', 'photo'], street: ['Reading', 'ListeningMusic'],
};

export function outfitPoses(outfit: string | null): string[] {
  return outfit && actions[outfit] ? [outfit, ...actions[outfit]] : actions.street;
}

export class PoseCache {
  private queue = new Map<string, {promise: Promise<StreetPose>; resolve: (pose: StreetPose) => void; reject: (reason: unknown) => void}>();
  private background: string[] = [];
  private scheduled = 0;
  private activeLoad: string | null = null;
  private closed = false;
  private pinned = new Set(bodies);
  private used = new Map<string, number>();
  private serial = 0;
  private totalBytes = 0;
  private loads = 0;
  private readonly limit = 96 * 1024 * 1024;

  constructor(private readonly poses: Map<string,StreetPose>, private readonly errors: Record<string,string>,
    private readonly load: (name: string) => Promise<StreetPose>) {}

  get stats() { return {bytes: this.totalBytes, limit: this.limit, loads: this.loads, loading: this.activeLoad}; }

  protect(...names: (string | undefined)[]): void {
    this.pinned = new Set([...bodies, ...names.filter((name): name is string => !!name)]);
    this.prune();
  }

  cancelWaiting(): void {
    for (const [name, task] of this.queue) {
      if (name === this.activeLoad) continue;
      task.reject(Error('Replaced by a newer request'));
      this.queue.delete(name);
    }
  }

  request(name: string): Promise<StreetPose> {
    const cached = this.poses.get(name);
    if (cached) {
      this.used.set(name, ++this.serial);
      return Promise.resolve(cached);
    }
    const existing = this.queue.get(name);
    if (existing) return existing.promise;
    let resolve!: (pose: StreetPose) => void, reject!: (reason: unknown) => void;
    const promise = new Promise<StreetPose>((ok, fail) => { resolve=ok; reject=fail; });
    this.queue.set(name, {promise,resolve,reject});
    clearTimeout(this.scheduled);
    this.scheduled = 0;
    this.run();
    return promise;
  }

  warm(outfit: string | null): void {
    this.background = [...new Set([...outfitPoses(outfit), ...bodies])];
    this.run();
  }

  private run(): void {
    if (this.closed || this.activeLoad || this.scheduled) return;
    // New requests take priority over queued warming. Finish the one upload in flight.
    const name = [...this.queue.keys()].reverse()[0] ?? this.background.find(key => !this.poses.has(key) && !this.errors[key]);
    if (!name) return;
    if (!this.queue.has(name)) {
      this.scheduled = window.setTimeout(() => { this.scheduled = 0; this.fetch(name); }, 90);
    } else this.fetch(name);
  }

  private fetch(name: string): void {
    this.activeLoad = name;
    this.load(name).then(pose => {
      if (this.closed) { pose.dispose(); return; }
      this.poses.set(name, pose);
      delete this.errors[name];
      this.used.set(name, ++this.serial);
      this.totalBytes += pose.bytes;
      this.loads++;
      this.queue.get(name)?.resolve(pose);
      this.prune(name);
    }).catch(error => {
      if (!this.closed) this.errors[name] = String(error);
      this.queue.get(name)?.reject(error);
    }).finally(() => {
      this.background = this.background.filter(key => key !== name);
      this.queue.delete(name);
      this.activeLoad = null;
      this.run();
    });
  }

  private prune(recent?: string): void {
    for (const [key] of [...this.used].sort((a,b) => a[1]-b[1])) {
      if (this.totalBytes <= this.limit) break;
      if (key === recent || this.pinned.has(key) || this.queue.has(key)) continue;
      const pose = this.poses.get(key)!;
      pose.dispose();
      this.totalBytes -= pose.bytes;
      this.poses.delete(key);
      this.used.delete(key);
    }
  }

  dispose(): void {
    this.closed = true;
    clearTimeout(this.scheduled);
    for (const task of this.queue.values()) task.reject(Error('Model closed'));
    this.queue.clear();
  }
}
