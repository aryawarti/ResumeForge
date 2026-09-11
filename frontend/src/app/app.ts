import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { API } from './core/api.config';
import { AuthService } from './core/auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private auth = inject(AuthService);

  readonly signedIn = this.auth.isAuthenticated;
  readonly user = this.auth.user;

  /** Falls back to the local part of the email until /me resolves. */
  readonly who = computed(() => {
    const user = this.user();
    if (!user) return '';
    return user.display_name || user.email.split('@')[0];
  });

  private loaded = signal(false);

  constructor() {
    // Render's free plan sleeps the API after 15 idle minutes and needs about a
    // minute to wake. Starting that on page load means it is usually awake by
    // the time someone has typed a password. Nothing waits on the result.
    fetch(`${API}/health`).catch(() => undefined);

    // A reload restores the token from storage but not the user behind it.
    effect(() => {
      if (this.signedIn() && !this.loaded()) {
        this.loaded.set(true);
        this.auth.loadUser().subscribe({ error: () => undefined });
      }
      if (!this.signedIn() && this.loaded()) {
        this.loaded.set(false);
      }
    });
  }

  signOut() {
    this.auth.logout();
  }
}
