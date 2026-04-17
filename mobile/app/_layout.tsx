import { useEffect, useState } from 'react';
import { SplashScreen, Slot, useRouter, useSegments } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { getToken } from '../lib/storage';
import { AuthContext } from '../lib/auth-context';
import { ThemeProvider } from '../lib/theme-context';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const router = useRouter();
  const segments = useSegments();

  useEffect(() => {
    getToken()
      .then((token) => setAuthed(!!token))
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (authed === null) return;
    SplashScreen.hideAsync();
    const inAuth = segments[0] === '(auth)';
    const inApp  = segments[0] === '(app)';
    if (!authed && !inAuth) router.replace('/(auth)/login');
    else if (authed && !inApp) router.replace('/(app)/chat');
  }, [authed, segments]);

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ThemeProvider>
        <AuthContext.Provider value={{ setAuthed }}>
          <Slot />
        </AuthContext.Provider>
      </ThemeProvider>
    </GestureHandlerRootView>
  );
}
