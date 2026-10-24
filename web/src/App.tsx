import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { authToken, onAuth, saveToken } from "./api";
import {
  Accounts,
  Folders,
  Groups,
  Inbox,
  Login,
  Opportunities,
  Rules,
  SearchPage,
  Senders,
  Settings,
  Shell,
  Todos,
  Usage,
} from "./screens";

export function App() {
  const [token, setToken] = useState(authToken());
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => onAuth(() => setToken(authToken())), []);
  useEffect(() => {
    if (!token && location.pathname !== "/") navigate("/");
  }, [token, location.pathname, navigate]);

  return (
    <Routes>
      <Route path="/" element={token ? <Navigate to="/inbox" replace /> : <Login />} />
      <Route element={token ? <Shell onLogout={() => saveToken(null)} /> : <Navigate to="/" replace />}>
        <Route path="/inbox" element={<Inbox lane="all" title="Inbox" />} />
        <Route path="/urgent" element={<Inbox lane="urgent" title="Urgent" />} />
        <Route path="/silenced" element={<Inbox lane="silenced" title="Silenced" />} />
        <Route path="/todo" element={<Todos />} />
        <Route path="/opportunities" element={<Opportunities />} />
        <Route path="/rules" element={<Rules />} />
        <Route path="/folders" element={<Folders />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/groups" element={<Groups />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/senders" element={<Senders />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
