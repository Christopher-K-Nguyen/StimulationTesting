function data_diff2 = diff2(data)
    % Calculate the first-order difference
    data_diff = diff(data);

    % Determine the size of the input data
    [row, col] = size(data);

    % Initialize the output to include the same size as the input
    if row == 1  % Row vector
        data_diff2 = zeros(1, col); % Ensure the output is a row vector
        data_diff2(1) = data(1);    % Keep the first element
        data_diff2(2:col) = data_diff; % Append the differences
    elseif col == 1  % Column vector
        data_diff2 = zeros(row, 1); % Ensure the output is a column vector
        data_diff2(1) = data(1);    % Keep the first element
        data_diff2(2:row) = data_diff; % Append the differences
    else
        error('Input must be a 1D array (row or column vector).');
    end
end
