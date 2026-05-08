function colLetter = num2ExcelCol(colNum)
    % Function to convert a column number to an Excel column letter
    %
    % Input:
    %   colNum: The column number (1 for A, 2 for B, ..., 27 for AA, etc.)
    %
    % Output:
    %   colLetter: The corresponding Excel column letter
    
    colLetter = ''; % Initialize the column letter string
    while colNum > 0
        remainder = mod(colNum - 1, 26);
        colLetter_alloc = [char(remainder + 'A'), colLetter];
        colLetter = colLetter_alloc;
        colNum = floor((colNum - 1) / 26);
    end
end
