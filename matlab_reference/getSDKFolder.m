function [] = getSDKFolder()

SDK_folder = 'MATLAB SDK for PlexStim 2.0 - 64 bit';                        % SDK folder name
SDK_location_Cdrive = 'C:\PlexonSDKs\MATLAB SDK for PlexStim 2.0 - 64 bit'; % SDK in :C
folderExists = 7;                                                           % folder existing value
fprintf('Searching for SDK in directory...');  % searching for SDK
if exist(SDK_folder,'dir') == folderExists  % SDK found in directory
    fprintf('OK\n');    	% SDK found
    path(path,SDK_folder);                  % adding SDK to path (just in case)
else                                                           	% SDK not found
    prompt = 'SDK NOT FOUND IN DIRECTORY';                  	% message prompt
    waitfor(msgbox(prompt,titleError));                         % SDK not found in directory
    fprintf('\nSearching for SDK in "%s"...\n',SDK_location_Cdrive);% searching for SDK in C:
    if exist(SDK_location_Cdrive,'dir') == folderExists         % SDK found
        fprintf('OK.\n');
        fprintf('Adding SDK location to directory.\n\n');              % SDK found in C:
        path(path,SDK_location_Cdrive);                         % adding SDK to directory
    else                                                % SDK not found
        fprintf('\nSDK NOT FOUND IN C:\n');                    % SDK not found in C:
        prompt1 = 'Find SDK folder to add to directory...';   % select folder into directory
        prompt2 = sprintf('"%s"',SDK_folder);               % folder to find
        fprintf('%s...',prompt1);                                      % print message
        waitfor(msgbox({prompt1,prompt2},titleError));	% message box
        SDK_location = uigetdir();                      % input folder location
        cancel_SDK_location = isempty(SDK_location);    % ccncel dialog
        if cancel_SDK_location == 1     % cancel detected
            fprintf('Quitting...\n\n'); % quitting
            return;                     % exit program
        else                            % path entered
            fprintf('OK\n');% path located
            path(path,SDK_location);	% adding SDK to directory
        end
    end
end

end