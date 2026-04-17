import * as SecureStore from 'expo-secure-store';

const TOKEN_KEY = 'anjo_token';
const USER_ID_KEY = 'anjo_user_id';

export async function saveAuth(token: string, userId: string) {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
  await SecureStore.setItemAsync(USER_ID_KEY, userId);
}

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function getUserId(): Promise<string | null> {
  return SecureStore.getItemAsync(USER_ID_KEY);
}

export async function clearAuth() {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  await SecureStore.deleteItemAsync(USER_ID_KEY);
}
