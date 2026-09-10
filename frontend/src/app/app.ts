import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

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
