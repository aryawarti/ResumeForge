import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { API } from './api.config';
import { ForgeService } from './forge.service';
import { ProgressEvent } from './models';

export interface ProgressUpdate {
  event: ProgressEvent | null;
  done: boolean;
  status?: string;
  error?: string;
}

/**
 * Live generation progress over Server-Sent Events.
 *
 * The server stores progress as rows rather than broadcasting from memory, so
 * a dropped connection is recoverable: the browser resends Last-Event-ID and
 * the stream resumes from there. That matters because the API runs on an
 * instance that can be restarted mid-generation, and a user watching a
 * sixty-second job should not be shown a blank bar when it happens.
 */
@Injectable({ providedIn: 'root' })
export class ProgressService {
  private forge = inject(ForgeService);

  watch(generationId: string): Observable<ProgressUpdate> {
    return new Observable<ProgressUpdate>((subscriber) => {
      let source: EventSource | null = null;
      let cancelled = false;

      this.forge.streamTicket(generationId).subscribe({
        next: ({ ticket }) => {
          if (cancelled) return;

          const url = `${API}/generations/${generationId}/events?ticket=${encodeURIComponent(ticket)}`;
          source = new EventSource(url);

          source.addEventListener('progress', (raw) => {
            const event = JSON.parse((raw as MessageEvent).data) as ProgressEvent;
            subscriber.next({ event, done: false });
          });

          source.addEventListener('done', (raw) => {
            const payload = JSON.parse((raw as MessageEvent).data) as {
              status: string;
              error: string;
            };
            subscriber.next({
              event: null,
              done: true,
              status: payload.status,
              error: payload.error,
            });
            source?.close();
            subscriber.complete();
          });

          source.onerror = () => {
            // EventSource reconnects on its own and replays from
            // Last-Event-ID; only a closed stream is terminal.
            if (source?.readyState === EventSource.CLOSED) {
              subscriber.error(new Error('progress stream closed'));
            }
          };
        },
        error: (err) => subscriber.error(err),
      });

      return () => {
        cancelled = true;
        source?.close();
      };
    });
  }
}
