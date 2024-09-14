// server.js

const express = require('express');
const app = express();
const port = 3000;
const OpenAI = require('openai');
require('dotenv').config();

app.use(express.json());

app.post('/openai', async (req, res) => {
  const { messages } = req.body;

  // Initialize OpenAI API with your API key
  const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
  });

  try {
    const response = await openai.chat.completions.create({
      model: 'gpt-4',
      messages: messages,
      max_tokens: 300,
    });

    res.json(response);
  } catch (error) {
    console.error('OpenAI API error:', error);
    res.status(500).json({ error: 'OpenAI API error' });
  }
});

app.post('/insert', async (req, res) => {
  const { productData, amazonData } = req.body;

  // Implement your database insertion logic here
  // For example, using a database like PostgreSQL, MongoDB, etc.

  try {
    // Insert productData and amazonData into your database
    // ...

    res.json({ message: 'Data inserted successfully' });
  } catch (error) {
    console.error('Database insertion error:', error);
    res.status(500).json({ error: 'Database insertion error' });
  }
});

app.listen(port, () => {
  console.log(`Server listening at http://localhost:${port}`);
});
