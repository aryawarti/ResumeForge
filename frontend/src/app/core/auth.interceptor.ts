import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { BehaviorSubject, Observable, throwError } from 'rxjs';
import { catchError, filter, switchMap, take } from 'rxjs/operators';

import { AuthService } from './auth.service';
import { API } from './api.config';

/**
 * Attaches the access token and recovers from expiry.
 *
 * The gate below is the part worth understanding. When a page issues several
 * requests at once and the access token has expired, every one of them gets a
 * 401 at roughly the same moment. Without coordination each would start its
 * own refresh, and since refresh tokens rotate server-side, the first to
 * return would invalidate the rest -- signing the user out in the middle of a
 * working session. So the first 401 opens the gate, the rest queue on it, and
 * all of them retry once with the single new token.
 */
let refreshing = false;
const gate = new BehaviorSubject<string | null>(null);

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  // Auth endpoints must not carry a stale token, or a refresh would 401.
  const isAuthCall =
    req.url.startsWith(`${API}/auth/login`) ||
    req.url.startsWith(`${API}/auth/register`) ||
    req.url.startsWith(`${API}/auth/refresh`);

  const token = auth.accessToken();
  const request = token && !isAuthCall ? withToken(req, token) : req;

  return next(request).pipe(
    catchError((error: unknown) => {
      const is401 = error instanceof HttpErrorResponse && error.status === 401;
      if (!is401 || isAuthCall || !auth.refreshToken()) {
        return throwError(() => error);
      }

      if (refreshing) {
        // Wait for the in-flight refresh, then retry once.
        return gate.pipe(
          filter((value): value is string => value !== null),
          take(1),
          switchMap((fresh) => next(withToken(req, fresh))),
        );
      }

      refreshing = true;
      gate.next(null);

      return auth.refresh().pipe(
        switchMap((pair) => {
          refreshing = false;
          gate.next(pair.access_token);
          return next(withToken(req, pair.access_token));
        }),
        catchError((refreshError: unknown) => {
          refreshing = false;
          // The refresh token is spent or revoked; this session is over.
          auth.clear();
          router.navigate(['/sign-in']);
          return throwError(() => refreshError);
        }),
      ) as Observable<never>;
    }),
  );
};

function withToken(req: Parameters<HttpInterceptorFn>[0], token: string) {
  return req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
}
