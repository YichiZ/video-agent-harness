// The HTTP routes in server/api/app.py. The WebSocket frames are in protocol.ts.
import type { IngestStatus } from "./protocol";

export interface Word {
  text: string;
  start: number;
  end: number;
  type: string;
  speaker: string | null;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  speaker: string | null;
  text: string;
}

export interface Transcript {
  language: string;
  speakers: string[];
  words: Word[];
  segments: Segment[];
}

export interface Shot {
  index: number;
  start_s: number;
  end_s: number;
  caption?: string;
}

export const proxyUrl = (videoId: string) => `/video/current/proxy?v=${videoId}`;
export const thumbUrl = (videoId: string, shot: number) =>
  `/video/current/thumbs/${shot}.jpg?v=${videoId}`;

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}

export async function getStatus(): Promise<IngestStatus | null> {
  const response = await fetch("/video/current/status");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`status: ${response.status}`);
  return response.json();
}

export const getTranscript = () => getJson<Transcript>("/video/current/transcript");
export const getShots = () =>
  getJson<{ captioned: boolean; shots: Shot[] }>("/video/current/shots");

/** POST the file as the raw body; XHR because fetch has no upload progress. */
export function uploadVideo(file: File, onProgress: (fraction: number) => void) {
  return new Promise<IngestStatus>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/video?name=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status === 200) resolve(JSON.parse(xhr.responseText));
      else {
        let detail = xhr.responseText;
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          // not JSON: keep the text
        }
        reject(new Error(detail || `upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.send(file);
  });
}
