function [File,isQuit] = getChannels2_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS = [1 100];
% Options
opts.Default = BUTTON_CONFIRM;  % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
numOfDevices = length(File.Oscilloscope);
isQuit = false;

%% Channels
for deviceNum = 1:numOfDevices
    make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    resourceName = File.Oscilloscope(deviceNum).Resource;
    isUSB = contains2(resourceName,'usb');
    fprintf('Edit %s %s channels...',make,model);
    confirmChannels = false;
    while ~confirmChannels
        fprintf('\t');
        switch deviceNum
            case 1
                fprintf('Select additional oscilloscope channels...');
                channelSelect_cell = {'CH1';'CH2'};
                addChannel_cell = {'';'CH3';'CH4'};
                promptChannels = 'Select additional oscilloscope channels.';     % instruction
                titleChannels = 'Additional Oscilloscope Channel Selection';
            case 2
                fprintf('Select oscilloscope measurement channels...');
                channelSelect_cell = {};
                addChannel_cell = {'CH1';'CH2';'CH3';'CH4'};
                promptChannels = 'Select oscilloscope measurement channels.';     % instruction
                titleChannels = 'Oscilloscope Measurement Channel Selection';
        end

        % Dialog box
        if isUSB
            [list_idx,~] = listdlg(...	% list dialog
                'PromptString',promptChannels,... % list prompts
                'ListString',addChannel_cell,...    % list
                'Name',titleChannels,...                 % list title
                'ListSize',[220 60], ....
                'SelectionMode','multiple', ....
                'InitialValue',1);
            if list_idx == 1
                list_idx = [];
            end
        else
            [list_idx,~] = listdlg(...	% list dialog
                'PromptString',promptChannels,... % list prompts
                'ListString',addChannel_cell,...    % list
                'Name',titleChannels,...                 % list title
                'ListSize',[220 60], ....
                'SelectionMode','multiple', ....
                'InitialValue',[1 2]);           
        end
        
        % Collect input
        if isempty(list_idx)           % cancel detected
            fprintf('\nQuitting...'); % quitting
            isQuit = true;
            break;                     % exit program
        end

        % Confirm oscilloscope selection
        % Format questions
%         list_len = length(list_idx);
%         if list_len > 1 && ismember(1,list_idx)
%             list_idx(1) = [];
%         end
        numOfAddChannels = length(list_idx);
        if numOfAddChannels > 0
            addChannel = addChannel_cell(list_idx);
            channelSelect_cell_alloc = [channelSelect_cell;addChannel];
            channelSelect_cell = channelSelect_cell_alloc;
        end
        if ~isempty(addChannel_cell)
            channel_use = strjoin(addChannel,', ');
        else
            channel_use = 'NONE';
        end
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
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;                                 % exit program
    end

    %% Channel Names
    confirmChannelName = false;
    numOfChannels = length(channelSelect_cell);
    inputChannelList = cell(1,numOfChannels);
    for channel_idx = 1:numOfChannels
        channelName = channelSelect_cell{channel_idx};
        if numOfDevices == 1 || (numOfDevices == 2 && deviceNum == 1)
            switch channel_idx
                case 1
                    inputChannelList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be Voltage)}',channelName);
                case 2
                    inputChannelList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be Current)}',channelName);
                otherwise
                    inputChannelList{channel_idx} = channelName;
            end
        else
            inputChannelList{channel_idx} = channelName;
        end
    end

    switch numOfDevices
        case 1
            switch numOfChannels
                case 1
                    inputDefault = {'Voltage (V)'};
                case 2
                    inputDefault = {'Voltage (V)','Current (uA)'};
                case 3
                    inputDefault = {'Voltage (V)','Current (uA)','Active (V)'};
                case 4
                    inputDefault = {'Voltage (V)','Current (uA)','Active (V)','Return (V)'};
            end
        case 2
            switch deviceNum
                case 1
                    switch numOfChannels
                        case 1
                            inputDefault = {'Voltage (V)'};
                        case 2
                            inputDefault = {'Voltage (V)','Current (uA)'};
                        case 3
                            inputDefault = {'Voltage (V)','Current (uA)','Differential (V)'};
                        case 4
                            inputDefault = {'Voltage (V)','Current (uA)','Differential 1 (V)','Differential 2 (V)'};
                    end
                case 2
                    switch numOfChannels
                        case 1
                            inputDefault = {'Active (V)'};
                        case 2
                            inputDefault = {'Active (V)','Return (V)'};
                        case 3
                            inputDefault = {'Active (V)','Return 1 (V)','Return 2 (V)'};
                        case 4
                            inputDefault = {'Active (V)','Return 1 (V)','Return 2 (V)','Return 3 (V)'};
                    end
            end
    end

    while ~confirmChannelName
        fprintf('\t');
        fprintf('Enter oscilloscope channel names and include units for plot...');
        channelName_cell = inputdlg( ...
            inputChannelList, ...
            'Oscilloscope Channel Names with Units', ...
            DIMS, ...
            inputDefault, ...
            opts);
        if isempty(channelName_cell)
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
        end
        inputDefault = channelName_cell;
        questList = cell(1,numOfChannels);
        for channel_idx = 1:numOfChannels
            channel = channelSelect_cell{channel_idx};
            inputName = channelName_cell{channel_idx};
            if deviceNum == 1 && channel_idx == 2
                if contains2(inputName,'curr')
                    questList{channel_idx} = sprintf('%s: {\\bf%s}', ...
                        channel,inputName);
                else
                    questList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be in Current)}: {\\bf%s}', ...
                        channel,inputName);
                end
            else
                questList{channel_idx} = sprintf('%s: {\\bf%s}', ...
                    channel,inputName);
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
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;                                 % exit program
    end

    %% Channel Scalings
    channelScaling_arr = ones(numOfChannels,1);
    channelScaling_cell = cell(1,numOfChannels);
    switch deviceNum
        case 1
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
        case 2
            defaultScaling = cell(1,numOfChannels);
            defaultScaling(:) = {'1'};
    end
    for channel_idx = 1:numOfChannels
        channel = channelSelect_cell{channel_idx};
        channelName = channelName_cell{channel_idx};
        channelScaling_cell{channel_idx} = sprintf('%s %s',channel,channelName);
    end

    confirmScaling = false;
    while ~confirmScaling
        fprintf('\t');
        fprintf('Enter oscilloscope channel scalings...');
        inputScaling_cell = inputdlg( ...
            channelScaling_cell, ...
            'Oscilloscope Channel Scalings (Monitor in V / Output Unit)', ...
            DIMS, ...
            defaultScaling, ...
            opts);
        if isempty(inputScaling_cell)
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
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
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;                                 % exit program
    end

    %% Store
    File.Oscilloscope(deviceNum).NumberOfChannels = numOfChannels;
    File.Oscilloscope(deviceNum).Channels = channelSelect_cell;
    File.Oscilloscope(deviceNum).ChannelNames = channelName_cell;
    File.Oscilloscope(deviceNum).Scalings = channelScaling_arr;
    units_cell = cell(numOfChannels,1);
    for scopeChannelNum = 1:numOfChannels
        channelName = channelName_cell{scopeChannelNum};
        start_idx = strfind(channelName,'(') + 1;
        end_idx = strfind(channelName,')') - 1;
        units_cell{scopeChannelNum} = channelName(start_idx:end_idx);
    end
    File.Oscilloscope(deviceNum).Units = units_cell;
end

end