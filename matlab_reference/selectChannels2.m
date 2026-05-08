function [File,isQuit] = selectChannels2(File,varargin)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Values

%% Variables
confirmChannels = false;
isQuit = false;
numOfChannels = File.Parameters.NumberOfChannels;
numOfVar = length(varargin);
if numOfVar > 0
    fprintf('Enter channel selection...');
    var1 = varargin{1};
    className = class(var1);
    switch className
        case 'double'
            selectedChannels = var1;
        case 'char'
            if contains2(var1,'all')
                selectedChannels = 1:numOfChannels;
            end
    end
    fprintf('OK.\n\n');  % info confirmed
else
    %% Function
    while ~confirmChannels
        fprintf('Enter channel selection...');

        % Channel list
        listChannel = strings(numOfChannels,1);
        for channelNum = 1:numOfChannels
            listChannel(channelNum) = sprintf('Channel %02d',channelNum);
        end
        promptQuestListCh_cell = cellstr(listChannel);

        % Dialog box
        titleListCh = 'Channel Selection';      % list title
        promptListCh = {...                     % list prompts
            'Select channels to stimulate.',... % insrtuction
            'Hold CTRL for multi-select.'};     % multi-select
        [selectedChannels,listChannel_tf] = listdlg(...	% list dialog
            'PromptString',promptListCh,... % list prompts
            'ListString',promptQuestListCh_cell,...    % list
            'Name',titleListCh, ...
            'InitialValue',1:numOfChannels, ...
            'SelectionMode','multiple', ...
            'ListSize',[160 240]);           % list title

        % Collect input
        numOfSelectedChannels = length(selectedChannels);
        if listChannel_tf == false             % cancel detected
            fprintf('\nQuitting...'); % quitting
            isQuit = true;
            return;                     % exit program
        end

        % Confirm channel selection
        titleQuestListCh = 'Confirm Channel Selection';
        % Format questions
        channelSelect_cell = cell(numOfSelectedChannels,1);
        for channel_idx = selectedChannels
            channelName = listChannel(channel_idx);
            channelSelect_cell{channel_idx} = sprintf('{\\bf%s}',channelName);
        end
        promptQuestListCh = ['Channels selected:';channelSelect_cell];
        % Question box
        questListCh = questdlg(...                      % question dialog
            promptQuestListCh,...                       % question prompts
            titleQuestListCh,...                        % question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questListCh                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmChannels = true;               % confirm info
                fprintf('OK\n');  % info confirmed
            case BUTTON_TRY                             % try again
                confirmChannels = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                return;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                return;                                 % exit program
        end
    end
end

%% Store
File.Parameters.Channels.Test = selectedChannels;
if numOfSelectedChannels == 1
    File.Parameters.Channels.Plexon(:) = 1;
end

end