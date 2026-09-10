import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';

import { TokenPair, User } from './models';
import { API } from './api.config';

const ACCESS_KEY = 'forge.access';
const REFRESH_KEY = 'forge.refresh';

/**
 * Holds the token pair and the current user.
 *
 * Tokens live in localStorage so a reload does not sign you out. The refresh
 * token is rotated server-side on every use, so a stale copy is useless to
 * anyone who lifts it after the fact.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);

  readonly accessToken = signal<string | null>(read(ACCESS_KEY));
  readonly refreshToken = signal<string | null>(read(REFRESH_KEY));
  readonly user = signal<User | null>(null);
  readonly isAuthenticated = computed(() => this.accessToken() !== null);

  register(email: string, password: string, displayName: string) {
    return this.http
      .post<TokenPair>(`${API}/auth/register`, {
        email,
        password,
        display_name: displayName,
      })
      .pipe(tap((pair) => this.store(pair)));
  }

  login(email: string, password: string) {
    return this.http
      .post<TokenPair>(`${API}/auth/login`, { email, password })
      .pipe(tap((pair) => this.store(pair)));
  }

  /** Exchange the refresh token for a new pair. */
  refresh(): Observable<TokenPair> {
    return this.http
      .post<TokenPair>(`${API}/auth/refresh`, {
        refresh_token: this.refreshToken(),
      })
      .pipe(tap((pair) => this.store(pair)));
  }

  loadUser() {
    return this.http
      .get<User>(`${API}/auth/me`)
      .pipe(tap((user) => this.user.set(user)));
  }

  logout() {
    const token = this.refreshToken();
    if (token) {
      // Revoke server-side; a failure here still clears the client.
      this.http.post(`${API}/auth/logout`, { refresh_token: token }).subscribe({
        error: () => undefined,
      });
    }
    this.clear();
    this.router.navigate(['/sign-in']);
  }

  private store(pair: TokenPair) {
    this.accessToken.set(pair.access_token);
    this.refreshToken.set(pair.refresh_token);
    write(ACCESS_KEY, pair.access_token);
    write(REFRESH_KEY, pair.refresh_token);
  }

  clear() {
    this.accessToken.set(null);
    this.refreshToken.set(null);
    this.user.set(null);
    write(ACCESS_KEY, null);
    write(REFRESH_KEY, null);
  }
}

/* Storage can throw in private modes; a missing token is a valid state. */
function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}
