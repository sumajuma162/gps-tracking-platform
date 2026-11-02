import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { api } from "./api";
import { useAuth } from "../store/auth";

export interface Page<T> { count: number; next: string | null; previous: string | null; results: T[] }

/** GET with the active organization in the cache key, so tenants never share cached data. */
export function useApiQuery<T>(path: string, params?: Record<string, unknown>, options?: Partial<UseQueryOptions<T>>) {
  const orgId = useAuth((s) => s.activeOrgId);
  return useQuery<T>({
    queryKey: [orgId, path, params ?? {}],
    queryFn: async () => (await api.get<T>(path, { params })).data,
    ...options,
  });
}
