import { apiClient } from "../lib/api-client";

export const uploadApi = {
  // Uploads the raw file and gets back a short URL to store on the record —
  // never send a base64 data-URI to a JSON endpoint for this (that's what
  // used to happen here and it's why booking documents in Mongo ballooned
  // to ~1.4MB each, see backend/app/core/storage.py for the full story).
  photo: async (file: File): Promise<string> => {
    const formData = new FormData();
    formData.append("file", file);
    const { data } = await apiClient.post<{ data: { url: string } }>("/uploads/photo", formData, {
      // Let axios/the browser set the multipart boundary itself — the
      // client's default "application/json" header would otherwise win and
      // the server would never see a valid multipart body.
      headers: { "Content-Type": undefined },
    });
    return data.data.url;
  },
};
