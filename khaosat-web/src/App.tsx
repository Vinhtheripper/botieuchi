import{Routes,Route,Link}from'react-router-dom';import Survey from'./Survey';import Admin from'./Admin';
export default function App(){return <Routes><Route path="/" element={<Survey/>}/><Route path="/admin" element={<Admin/>}/><Route path="*" element={<main className="center"><h1>404</h1><Link to="/">Về khảo sát</Link></main>}/></Routes>}
