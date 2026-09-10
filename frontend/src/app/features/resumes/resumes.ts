import { Component, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ForgeService } from '../../core/forge.service';
import { Resume, ResumeDetail } from '../../core/models';

/**
 * Base resumes: upload, review the parse, delete.
 *
 * The parse summary after upload is not decoration. If the parser read four
 * sections where you have five, you need to know that here -- before spending
 * a generation on a document the system misread.
 */
@Component({
  selector: 'forge-resumes',
  standalone: true,
  imports: [FormsModule, RouterLink, DatePipe],
  template: `
    <header class="head">
      <div>
        <p class="eyebrow">Base resumes</p>
        <h1>Your sources</h1>
        <p class="head__note">
          Keep a version per track &mdash; a backend one, an ML one. Each is
          parsed and compiled on upload so problems surface now.
        </p>
      </div>
      <button class="btn" (click)="showUpload.set(!showUpload())">
        {{ showUpload() ? 'Cancel' : 'Add a resume' }}
      </button>
    </header>

    @if (showUpload()) {
      <section class="sheet upload">
        <label class="field">
          <span>Name this version</span>
          <input
            type="text"
            [(ngModel)]="name"
            placeholder="Backend engineer"
          />
        </label>

        <label class="field">
          <span>LaTeX source</span>
          <textarea
            rows="12"
            [(ngModel)]="source"
            placeholder="Paste your .tex file, or choose it below"
          ></textarea>
        </label>

        <div class="upload__actions">
          <input
            type="file"
            accept=".tex,text/plain"
            (change)="readFile($event)"
          />
          <button class="btn" (click)="upload()" [disabled]="busy() || !source.trim()">
            {{ busy() ? 'Parsing and compiling…' : 'Upload' }}
          </button>
        </div>

        @if (error()) {
          <p class="notice notice--error">{{ error() }}</p>
        }

        @if (lastUpload(); as up) {
          <div class="parsed">
            <p class="eyebrow">What we read</p>
            <p class="parsed__stats mono">
              {{ up.stats.sections }} sections ·
              {{ up.stats.entries }} entries ·
              {{ up.stats.bullets }} bullets · template
              <b>{{ up.template_profile }}</b>
            </p>
            <ul class="parsed__list">
              @for (s of up.sections; track s.id) {
                <li>
                  <span class="parsed__title">{{ s.title }}</span>
                  <span class="id">{{ s.entries }}e / {{ s.bullets }}b</span>
                </li>
              }
            </ul>
            @if (!up.compiles) {
              <p class="notice notice--warn">
                This file did not compile. It is saved, but tailoring it will
                likely fail until the source builds on its own.
              </p>
            }
          </div>
        }
      </section>
    }

    @if (resumes().length) {
      <ul class="list">
        @for (resume of resumes(); track resume.id) {
          <li class="card sheet">
            <div class="card__main">
              <a class="card__name" [routerLink]="['/resumes', resume.id]">
                {{ resume.name }}
              </a>
              <p class="card__meta mono">
                {{ resume.template_profile }} · {{ resume.page_count }} page{{
                  resume.page_count === 1 ? '' : 's'
                }}
                @if (!resume.compiles) {
                  · <span class="warn">does not compile</span>
                }
              </p>
            </div>
            <p class="card__date id">{{ resume.created_at | date: 'd MMM y' }}</p>
            <a class="btn btn--ghost" [routerLink]="['/tailor', resume.id]">Tailor</a>
            <button
              class="btn btn--danger"
              (click)="remove(resume)"
              [attr.aria-label]="'Delete ' + resume.name"
            >
              Delete
            </button>
          </li>
        }
      </ul>
    } @else if (!loading()) {
      <div class="empty sheet">
        <h3>No resumes yet</h3>
        <p>Add your <code>.tex</code> file to start tailoring against postings.</p>
      </div>
    }
  `,
  styles: [
    `
      .head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 2rem;
        padding: 2.5rem 0 1.75rem;
      }

      h1 {
        margin: 0.25rem 0 0.375rem;
        font-family: var(--mono);
        font-size: 1.75rem;
        letter-spacing: -0.03em;
      }

      .head__note {
        margin: 0;
        max-width: 52ch;
        font-size: 0.875rem;
        color: var(--graphite);
      }

      .upload {
        padding: 1.5rem;
        margin-bottom: 1.75rem;
      }

      .upload__actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        flex-wrap: wrap;
      }

      input[type='file'] {
        font-size: 0.8125rem;
        color: var(--graphite);
      }

      .parsed {
        margin-top: 1.5rem;
        padding-top: 1.25rem;
        border-top: 1px solid var(--rule);
      }

      .parsed__stats {
        margin: 0.375rem 0 0.875rem;
        color: var(--graphite);
      }

      .parsed__stats b {
        color: var(--ink);
      }

      .parsed__list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 1px;
        background: var(--rule);
        border: 1px solid var(--rule);
      }

      .parsed__list li {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 1rem;
        padding: 0.5rem 0.75rem;
        background: var(--sheet);
      }

      .parsed__title {
        font-family: var(--serif);
        font-size: 0.9375rem;
      }

      .list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 0.625rem;
      }

      .card {
        display: grid;
        grid-template-columns: 1fr auto auto auto;
        align-items: center;
        gap: 1.25rem;
        padding: 0.875rem 1.125rem;
      }

      .card__name {
        font-family: var(--mono);
        font-size: 0.9375rem;
        font-weight: 500;
        color: var(--ink);
        text-decoration: none;
      }

      .card__name:hover {
        text-decoration: underline;
      }

      .card__meta {
        margin: 0.2rem 0 0;
        font-size: 0.75rem;
        color: var(--faint);
      }

      .warn {
        color: var(--stet);
      }

      .card__date {
        white-space: nowrap;
      }

      code {
        font-family: var(--mono);
        font-size: 0.9em;
      }

      @media (max-width: 720px) {
        .card {
          grid-template-columns: 1fr auto;
        }
        .head {
          flex-direction: column;
        }
      }
    `,
  ],
})
export class Resumes {
  private forge = inject(ForgeService);

  resumes = signal<Resume[]>([]);
  lastUpload = signal<ResumeDetail | null>(null);
  showUpload = signal(false);
  busy = signal(false);
  loading = signal(true);
  error = signal('');

  name = '';
  source = '';

  constructor() {
    this.load();
  }

  private load() {
    this.forge.listResumes().subscribe({
      next: (list) => {
        this.resumes.set(list);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  readFile(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    file.text().then((text) => {
      this.source = text;
      if (!this.name) this.name = file.name.replace(/\.tex$/i, '');
    });
  }

  upload() {
    this.error.set('');
    this.busy.set(true);
    this.forge.uploadResume(this.name || 'Untitled', this.source).subscribe({
      next: (detail) => {
        this.busy.set(false);
        this.lastUpload.set(detail);
        this.name = '';
        this.source = '';
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? 'Upload failed.');
      },
    });
  }

  remove(resume: Resume) {
    // Generated versions are independent and survive this.
    if (!confirm(`Delete "${resume.name}"? Resumes you already tailored are kept.`))
      return;
    this.forge.deleteResume(resume.id).subscribe({
      next: () => this.resumes.update((list) => list.filter((r) => r.id !== resume.id)),
    });
  }
}
