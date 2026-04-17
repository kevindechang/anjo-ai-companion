import { View } from 'react-native';

// Root index — rendered briefly while _layout.tsx resolves auth and redirects.
// Never seen by the user; _layout redirects to (auth)/login or (app)/chat.
export default function Index() {
  return <View style={{ flex: 1, backgroundColor: '#0a0a0a' }} />;
}
