import { Stack } from 'expo-router';

export default function AppLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: '#0a0a0a' },
        headerTintColor: '#fff',
        headerShadowVisible: false,
        contentStyle: { backgroundColor: '#0a0a0a' },
      }}
    >
      <Stack.Screen name="forget" options={{ presentation: 'transparentModal', headerShown: false }} />
    </Stack>
  );
}
