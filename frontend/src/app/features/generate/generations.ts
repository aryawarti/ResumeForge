import { Component, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';

import { ForgeService } from '../../core/forge.service';
import { Generation } from '../../core/models';

/**
 * Every version ever generated, grouped by company.
 *
 * Nothing is overwritten by a regeneration, so v1 and v4 both sit here. When a
 * callback comes three weeks later you need to know which one they are
 * holding, and that is only answerable if the old ones still exist.
 */
@Component({
  selector: 'forge-generations',
  standalone: true,
  imports: [RouterLink, DatePipe],
  template: `
    <header class="head">
      <p class="eyebrow">Applications</p>
      <h1>Everything you have sent</h1>
    </header>

    @if (grouped().length) {
      @for (group of grouped(); track group.company) {
        <section class="group">
          <h2 class="group__name">{{ group.company || 'No company recorded' }}</h2>
          <ul class="list">
            @for (gen of group.items; track gen.id) {
              <li class="card sheet">
                <a class="card__role" [routerLink]="['/generations', gen.id]">
                  {{ gen.role || 'Untitled role' }}
                </a>
                <span class="pill mono">v{{ gen.version_no }}</span>
                <span class="card__status mono" [attr.data-status]="gen.status">
                  {{ gen.status }}
                </span>
                <span class="card__score mono">
                  @if (gen.status === 'completed') {
                    {{ (gen.coverage_score * 100).toFixed(0) }}%
                  }
                </span>
                <span class="id">{{ gen.created_at | date: 'd MMM' }}</span>
                <button class="btn btn--danger" (click)="remove(gen)">Delete</button>
              </li>
            }
          </ul>
        </section>
      }
    } @else if (!loading()) {
      <div class="empty sheet">
        <h3>Nothing tailored yet</h3>
        <p>Pick a base resume and paste a posting to make your first version.</p>
      </div>
    }
  `,
  styles: [
    `
      .head {
        padding: 2.5rem 0 1.5rem;
      }

      h1 {
        margin: 0.25rem 0 0;
        font-family: var(--mono);
        font-size: 1.75rem;
        letter-spacing: -0.03em;
      }

      .group {
        margin-bottom: 2rem;
      }

      .group__name {
        font-family: var(--mono);
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--graphite);
        padding-bottom: 0.5rem;
        margin-bottom: 0.625rem;
        border-bottom: 1px solid var(--rule);
      }

      .list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 0.5rem;
      }

      .card {
        display: grid;
        grid-template-columns: 1fr auto auto 3rem auto auto;
        align-items: center;
        gap: 1rem;
        padding: 0.75rem 1.125rem;
      }

      .card__role {
        font-family: var(--serif);
        font-size: 0.9375rem;
        color: var(--ink);
        text-decoration: none;
      }

      .card__role:hover {
        text-decoration: underline;
      }

      .pill {
        font-size: 0.6875rem;
        padding: 0.1rem 0.4rem;
        border: 1px solid var(--rule-strong);
        border-radius: 2px;
        color: var(--graphite);
      }

      .card__status {
        font-size: 0.6875rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--faint);
      }

      .card__status[data-status='completed'] {
        color: var(--keep);
      }

      .card__status[data-status='failed'] {
        color: var(--strike);
      }

      .card__status[data-status='running'] {
        color: var(--insert);
      }

      .card__score {
        font-size: 0.8125rem;
        text-align: right;
        color: var(--graphite);
      }

      @media (max-width: 760px) {
        .card {
          grid-template-columns: 1fr auto auto;
        }
      }
    `,
  ],
})
export class Generations {
  private forge = inject(ForgeService);

  generations = signal<Generation[]>([]);
  loading = signal(true);

  readonly grouped = computed(() => {
    const buckets = new Map<string, Generation[]>();
    for (const gen of this.generations()) {
      const list = buckets.get(gen.company) ?? [];
      list.push(gen);
      buckets.set(gen.company, list);
    }
    return [...buckets.entries()].map(([company, items]) => ({
      company,
      items: items.sort((a, b) => b.version_no - a.version_no),
    }));
  });

  constructor() {
    this.forge.listGenerations().subscribe({
      next: (list) => {
        this.generations.set(list);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  remove(gen: Generation) {
    if (!confirm(`Delete version ${gen.version_no}? Your base resume is kept.`)) return;
    this.forge.deleteGeneration(gen.id).subscribe({
      next: () => this.generations.update((l) => l.filter((g) => g.id !== gen.id)),
    });
  }
}
