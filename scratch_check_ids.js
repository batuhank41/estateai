const { PrismaClient } = require("@prisma/client");
const prisma = new PrismaClient();

async function main() {
  const companies = await prisma.$queryRawUnsafe("SELECT id, name FROM companies");
  console.log("COMPANIES:", JSON.stringify(companies, null, 2));
  
  const users = await prisma.$queryRawUnsafe("SELECT id, company_id, full_name FROM users LIMIT 10");
  console.log("USERS:", JSON.stringify(users, null, 2));
}

main().catch(console.error).finally(() => prisma.$disconnect());
