/**
 * Framework-agnostic Reactive Observable Store (compatible with React useSyncExternalStore).
 * Zero React or browser API dependencies.
 */

export type UnsubscribeFn = () => void;
export type StoreListener<T> = (snapshot: T) => void;

export class ObservableStore<T> {
  private current: T;
  private readonly listeners = new Set<() => void>();
  private readonly snapshotListeners = new Set<StoreListener<T>>();

  public constructor(initial: T) {
    this.current = initial;
  }

  public readonly getSnapshot = (): T => this.current;

  public readonly subscribe = (listener: () => void): UnsubscribeFn => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  public readonly subscribeWithValue = (listener: StoreListener<T>): UnsubscribeFn => {
    this.snapshotListeners.add(listener);
    return () => {
      this.snapshotListeners.delete(listener);
    };
  };

  public setState(next: T): void {
    if (Object.is(this.current, next)) {
      return;
    }
    this.current = next;
    for (const listener of this.listeners) {
      listener();
    }
    for (const listener of this.snapshotListeners) {
      listener(this.current);
    }
  }

  public update(updater: (prev: T) => T): T {
    const next = updater(this.current);
    this.setState(next);
    return next;
  }
}
