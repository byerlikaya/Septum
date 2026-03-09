import { redirect } from "next/navigation";

export default function HomePage(): null {
  redirect("/chat");
  return null;
}

