function [File,quitProgram] = getChannels_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS = [1 50];
% Options
opts.Default = BUTTON_CONFIRM;  % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
quitProgram = false;

%% Channels
confirmChannels = false;
channelSelect_cell = {'CH1';'CH2'};
addChannel_cell = {'CH3';'CH4'};
while ~confirmChannels
    fprintf('Select additional oscilloscope channels...');
    % Dialog box
    promptChannels = 'Select additional oscilloscope channels.';     % instruction
    [list_idx,~] = listdlg(...	% list dialog
        'PromptString',promptChannels,... % list prompts
        'ListString',addChannel_cell,...    % list
        'Name','Additional Oscilloscope Channel Selection',...                 % list title
        'ListSize',[140 80], ....
        'InitialValue',[1 2]);

    % Collect input
    if isempty(list_idx)           % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = true;
        break;                     % exit program
    end

    % Confirm oscilloscope selection
    % Format questions
    numOfAddChannels = length(list_idx);
    if numOfAddChannels > 1
        addChannel = addChannel_cell(list_idx);
        channelSelect_cell_alloc = [channelSelect_cell;addChannel];
        channelSelect_cell = channelSelect_cell_alloc;
    end
    numOfChannels = length(channelSelect_cell);
    channel_use = strjoin(addChannel_cell,', ');
    channelPrompt = sprintf('Additional oscilloscope channels: {\\bf%s}',channel_use);
    % Question box
    questChannels = questdlg(...                      % question dialog
        channelPrompt,...                       % question prompts
        'Confirm Additional Oscilloscope Channel Selection',...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questChannels                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmChannels = true;               % confirm info
            fprintf('%s\n',channel_use);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmChannels = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;                                 % exit program
end

%% Channel Names
confirmChannelName = false;
inputChannelList = cell(1,numOfChannels);
for channel_idx = 1:numOfChannels
    channelName = channelSelect_cell{channel_idx};
    if channel_idx <= 2
        switch channel_idx
            case 1
                inputChannelList{channel_idx} = sprintf( ...
                    '%s {\\bf(Should be in Voltage)}',channelName);
            case 2
                inputChannelList{channel_idx} = sprintf( ...
                    '%s {\\bf(Should be in Current)}',channelName);
        end
    else
        inputChannelList{channel_idx} = channelName;
    end
end
switch numOfChannels
    case 1
        inputDefault = {'Voltage (V)'};
    case 2
        inputDefault = {'Voltage (V)','Current (V)'};
    case 3
        inputDefault = {'Voltage (V)','Current (uA)','Working (V)'};
    case 4
        inputDefault = {'Voltage (V)','Current (uA)','Working (V)','Counter (V)'};
end
while ~confirmChannelName
    fprintf('Enter oscilloscope channel names and include units for plot...');
    channelName_cell = inputdlg( ...
        inputChannelList, ...
        'Oscilloscope Channel Names with Units', ...
        DIMS, ...
        inputDefault, ...
        opts);
    if isempty(channelName_cell)
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        break;                                 % exit program
    end
    inputDefault = channelName_cell;
    questList = cell(1,numOfChannels);
    for channel_idx = 1:numOfChannels
        channel = channelSelect_cell{channel_idx};
        inputName = channelName_cell{channel_idx};
        switch channel_idx
            case 1
                if contains2(inputName,'volt')
                    questList{channel_idx} = sprintf( ...
                        '%s: {\\bf%s}',channel,inputName);
                else
                    questList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be in Voltage)}: {\\bf%s}', ...
                        channel,inputName);
                end
            case 2
                if contains2(inputName,'curr')
                    questList{channel_idx} = sprintf( ...
                        '%s: {\\bf%s}',channel,inputName);
                else
                    questList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be in Current)}: {\\bf%s}', ...
                        channel,inputName);
                end
            otherwise
                questList{channel_idx} = sprintf( ...
                    '%s: {\\bf%s}',channel,inputName);
        end
    end
    questNames = questdlg( ...
        questList, ...
        'Confirm Oscilloscope Channel Names with Units', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);
    % Confirmation
    switch questNames                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmChannelName = true;               % confirm info
            channelName_char = strjoin(channelName_cell,', ');
            fprintf('%s\n',channelName_char);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmChannelName = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;                                 % exit program
end

%% Channel Scalings
confirmScaling = false;
channelScaling_arr = ones(numOfChannels,1);
channelScaling_cell = cell(1,numOfChannels);
switch numOfChannels
    case 1
        defaultScaling = {'1'};
    case 2
        defaultScaling = {'1','0.001'};
    case 3
        defaultScaling = {'1','0.001','1'};
    case 4
        defaultScaling = {'1','0.001','1','1'};
end
for channel_idx = 1:numOfChannels
    channel = channelSelect_cell{channel_idx};
    channelName = channelName_cell{channel_idx};
    channelScaling_cell{channel_idx} = sprintf('%s %s',channel,channelName);
end
while ~confirmScaling
    fprintf('Enter oscilloscope channel scalings...');
    inputScaling_cell = inputdlg( ...
        channelScaling_cell, ...
        'Oscilloscope Channel Scalings (Monitor in V / Output Unit)', ...
        DIMS, ...
        defaultScaling, ...
        opts);
    if isempty(inputScaling_cell)
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        break;                                 % exit program
    end
    defaultScaling = inputScaling_cell;
    questList = cell(1,numOfChannels);
    for channel_idx = 1:numOfChannels
        channel = channelSelect_cell{channel_idx};
        channelName = channelName_cell{channel_idx};
        inputScaling = inputScaling_cell{channel_idx};
        questList{channel_idx} = sprintf('%s %s: {\\bf%s}',channel,channelName,inputScaling);
        scaling = str2double(inputScaling);
        channelScaling_arr(channel_idx) = scaling;
    end
    questNames = questdlg( ...
        questList, ...
        'Confirm Oscilloscope Channel Names', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);
    % Confirmation
    switch questNames                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmScaling = true;               % confirm info
            channelScaling_char = strjoin(inputScaling_cell,', ');
            fprintf('%s\n',channelScaling_char);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmScaling = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;                                 % exit program
end

%% Store
File.Oscilloscope.NumberOfChannels = numOfChannels;
File.Oscilloscope.Channels = channelSelect_cell;
File.Oscilloscope.ChannelNames = channelName_cell;
File.Oscilloscope.Scalings = channelScaling_arr;

end