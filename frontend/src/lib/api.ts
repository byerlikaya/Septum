import axios from "axios";

const fallbackBaseURL = "http://localhost:8000";

const baseURL =
  process.env.NEXT_PUBLIC_API_URL && process.env.NEXT_PUBLIC_API_URL.trim().length > 0
    ? process.env.NEXT_PUBLIC_API_URL
    : fallbackBaseURL;

export const api = axios.create({
  baseURL
});

export default api;

