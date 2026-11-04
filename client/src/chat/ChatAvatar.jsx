import { UserRound } from 'lucide-react';
import { Avatar, AvatarFallback } from '../components/ui/avatar.jsx';
import LogoMark from '../LogoMark.jsx';

export default function ChatAvatar({ user = false, thinking = false }) {
  return (
    <Avatar className={`chat-avatar ${user ? 'user-avatar' : 'agent-avatar'}${thinking ? ' is-thinking' : ''}`} aria-hidden="true">
      <AvatarFallback>{user ? <UserRound size={16} /> : <LogoMark />}</AvatarFallback>
    </Avatar>
  );
}
