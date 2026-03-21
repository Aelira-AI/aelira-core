import axios, { AxiosInstance } from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'https://api.aelira.ai';

export function createLTIClient(accessToken: string): AxiosInstance {
  return axios.create({
    baseURL: API_BASE,
    headers: {
      'Authorization': `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
  });
}
