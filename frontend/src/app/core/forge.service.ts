import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import { API } from './api.config';
import {
  GapRoadmap,
  Generation,
  GenerationDetail,
  Resume,
  ResumeDetail,
} from './models';

/** Everything the app asks of the API, other than authentication. */
@Injectable({ providedIn: 'root' })
export class ForgeService {
  private http = inject(HttpClient);

  listResumes() {
    return this.http.get<Resume[]>(`${API}/resumes`);
  }

  getResume(id: string) {
    return this.http.get<ResumeDetail>(`${API}/resumes/${id}`);
  }

  uploadResume(name: string, texSource: string) {
    return this.http.post<ResumeDetail>(`${API}/resumes`, {
      name,
      tex_source: texSource,
    });
  }

  deleteResume(id: string) {
    return this.http.delete<void>(`${API}/resumes/${id}`);
  }

  listGenerations() {
    return this.http.get<Generation[]>(`${API}/generations`);
  }

  getGeneration(id: string) {
    return this.http.get<GenerationDetail>(`${API}/generations/${id}`);
  }

  createGeneration(body: {
    base_resume_id: string;
    job_text: string;
    job_url: string;
    company: string;
    role: string;
  }) {
    return this.http.post<Generation>(`${API}/generations`, body);
  }

  deleteGeneration(id: string) {
    return this.http.delete<void>(`${API}/generations/${id}`);
  }

  /** EventSource cannot send headers, so the stream needs its own ticket. */
  streamTicket(id: string) {
    return this.http.post<{ ticket: string }>(
      `${API}/generations/${id}/stream-ticket`,
      {},
    );
  }

  gapRoadmap() {
    return this.http.get<GapRoadmap>(`${API}/gaps/roadmap`);
  }
}
